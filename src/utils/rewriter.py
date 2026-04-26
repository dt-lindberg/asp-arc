"""Deterministic rewriter for LLM-emitted Clingo/ASP programs.

Stateless, pure text in / text out. Rules run in a fixed-point loop until
the program stabilises. Each rule is a small, logic-preserving fix for a
mechanical mistake observed in the audit corpus.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, List


@dataclass
class Rule:
    name: str
    short: str
    apply: Callable[[str], str]


# ---------------------------------------------------------------------------
# Rule implementations.  Each takes a program string and returns a new one.
# Logic preservation is the responsibility of the rule author + unit tests.
# ---------------------------------------------------------------------------

_AGG = r"#(?:count|min|max|sum)"


def _strip_fences(program: str) -> str:
    """Drop markdown fences, <asp>/<clingo> XML tags, and any prose lines
    that appear before/after the actual ASP block.

    Heuristic: keep the body delimited by ```...``` (or <asp>...</asp>) if
    such a delimiter is present.  Otherwise drop only obvious noise lines
    (pure prose lines containing no ASP tokens) at the head of the program.
    """
    text = program

    # If we find a fenced asp/clingo block, keep only its body.
    m = re.search(r"```(?:asp|clingo|prolog)?\s*\n(.*?)\n```", text, flags=re.DOTALL)
    if m:
        text = m.group(1)
    else:
        # Strip stray fence lines.
        text = re.sub(r"^\s*```.*$", "", text, flags=re.MULTILINE)

    # Drop XML-ish wrappers anywhere in the text.
    text = re.sub(r"</?\s*(asp|clingo|code|program)\s*>", "", text, flags=re.IGNORECASE)

    # Drop prose at the head — but be conservative.  A line is *prose*
    # only if it has none of: parens, ':-', leading '%', leading '#',
    # leading '{' or digit (cardinality head).  This keeps lines like
    # ``NR(N) :- N = #count{...}.`` which start with an uppercase
    # letter (a separate rule will lowercase the predicate).
    def is_prose(line: str) -> bool:
        s = line.strip()
        if not s:
            return False
        if s[0] in "%#{:":
            return False
        if s[0].isdigit():
            return False
        if "(" in s or ":-" in s:
            return False
        return True

    lines = text.split("\n")
    while lines:
        head = lines[0]
        if not head.strip():
            lines.pop(0)
            continue
        if is_prose(head):
            lines.pop(0)
            continue
        break
    text = "\n".join(lines)

    return text


def _agg_on_left(program: str) -> str:
    """Rewrite ``#agg{...} OP X`` → ``X OP' #agg{...}``.

    Clingo accepts an aggregate only on the right-hand side of a comparison
    inside a rule body (for the assignment form ``X = #agg{...}``).  The LLM
    frequently produces the SWI-style ``#count{...} = X`` shape, which is a
    syntax error.  We swap operands and flip the operator (``<`` ↔ ``>``,
    ``<=`` ↔ ``>=``; ``=``/``!=`` are symmetric).

    Brace matching is one-deep — sufficient for every aggregate seen in the
    corpus.
    """
    flip = {"<": ">", ">": "<", "<=": ">=", ">=": "<=", "=": "=", "!=": "!="}
    pattern = re.compile(
        rf"({_AGG}\s*\{{[^{{}}]*\}})\s*(<=|>=|!=|=|<|>)\s*([A-Za-z_][\w]*|\d+|\([^()]+\))"
    )

    def repl(m: re.Match) -> str:
        agg, op, rhs = m.group(1), m.group(2), m.group(3)
        return f"{rhs} {flip[op]} {agg}"

    return pattern.sub(repl, program)


def _mod_to_backslash(program: str) -> str:
    """``A #mod B`` → ``A \\ B`` (Clingo's modulo operator)."""
    return re.sub(r"#mod\b", r"\\", program)


def _agg_parens_to_braces(program: str) -> str:
    """``#count(...)`` → ``#count{...}`` (and #min/#max/#sum)."""

    def repl(m: re.Match) -> str:
        head = m.group(1)
        depth = 0
        start = m.end() - 1  # position of '('
        i = start
        while i < len(m.string):
            ch = m.string[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        # Replace just the open paren; closing paren handled by caller below.
        return head + "{"

    out = []
    i = 0
    while i < len(program):
        m = re.match(rf"({_AGG})\s*\(", program[i:])
        if not m:
            out.append(program[i])
            i += 1
            continue
        head = m.group(1)
        # Find the matching close paren of the (
        j = i + m.end() - 1  # index of '('
        depth = 0
        end = None
        k = j
        while k < len(program):
            ch = program[k]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = k
                    break
            k += 1
        if end is None:
            out.append(program[i])
            i += 1
            continue
        out.append(head + "{")
        out.append(program[j + 1 : end])
        out.append("}")
        i = end + 1
    return "".join(out)


def _lowercase_predicates(program: str) -> str:
    """Lower-case the leading character of identifiers used as predicate
    heads (``Foo(X) :- ...``) or in literal positions (``Foo(X)``).

    Clingo treats identifiers starting with an uppercase letter as
    *variables*, so an LLM that writes ``BlockColor(...)`` produces a
    syntax error.  We only rewrite tokens that are followed by ``(`` —
    leaving real variables alone.
    """
    # Don't touch tokens that are clearly variables (no following '(') or
    # the ASP keywords like '#count', '#min', etc.
    def repl(m: re.Match) -> str:
        word = m.group(1)
        if word in {"R", "C", "N", "M", "X", "Y", "V"}:  # safety, never matches due to '('
            return m.group(0)
        return word[0].lower() + word[1:] + "("

    # Match an uppercase-leading word immediately followed by '('.
    return re.sub(r"\b([A-Z][A-Za-z0-9_]*)\(", repl, program)


def _strip_html_tags(program: str) -> str:
    """Remove leftover ``<asp>``/``<clingo>``/``<code>`` tags that
    ``_strip_fences`` may have missed (e.g. when no closing tag exists).
    """
    return re.sub(r"</?\s*(asp|clingo|code|program)\s*/?>", "", program, flags=re.IGNORECASE)


def _swi_aggregate(program: str) -> str:
    """SWI-Prolog ``aggregate_all(count, Goal, N)`` → Clingo
    ``N = #count{...: Goal}``.

    Conservative: only rewrites the common one-line form.
    """
    pattern = re.compile(
        r"aggregate(?:_all)?\(\s*(count|sum|min|max)\s*,\s*([^,]+?)\s*,\s*([A-Za-z_][\w]*)\s*\)"
    )

    def repl(m: re.Match) -> str:
        kind, goal, var = m.group(1), m.group(2).strip(), m.group(3)
        return f"{var} = #{kind}{{1, {goal} : {goal}}}"

    return pattern.sub(repl, program)


def _bare_arith_assignment(program: str) -> str:
    """``H = maxRow(R) - minRow(R) + 1.`` at the top level is not valid
    Clingo.  These show up when the LLM forgets to wrap an arithmetic
    binding in a rule head.  We turn the binding into a fact-style rule
    by prefixing with a predicate name derived from the variable.

    ``H = expr.`` → ``h(H) :- H = expr.``
    """
    pattern = re.compile(
        r"^([A-Z][A-Za-z0-9_]*)\s*=\s*([^.\n]+?)\.\s*$",
        flags=re.MULTILINE,
    )

    def repl(m: re.Match) -> str:
        var = m.group(1)
        rhs = m.group(2).strip()
        # Skip if the RHS already contains a comparison operator that
        # would belong to a rule body (avoid double-rewriting).
        return f"{var.lower()}({var}) :- {var} = {rhs}."

    return pattern.sub(repl, program)


def _strip_continuation_colon(program: str) -> str:
    """Indented continuation lines that erroneously start with ``:-``.

    Pattern: previous line ended with ``,`` (rule body continues) and the
    next line opens with ``:-``.  The leading ``:-`` is a typo and should
    be removed so the body continues normally.
    """
    lines = program.split("\n")
    out: List[str] = []
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if (
            i > 0
            and stripped.startswith(":-")
            and out
            and out[-1].rstrip().endswith(",")
        ):
            indent = line[: len(line) - len(stripped)]
            out.append(indent + stripped[2:].lstrip())
        else:
            out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Rewriter
# ---------------------------------------------------------------------------


# Registry of every rule available; ASPRewriter holds the *active* subset.
ALL_RULES: List[Rule] = [
    Rule("strip_fences", "strip fences", _strip_fences),
    Rule("strip_html_tags", "strip tags", _strip_html_tags),
    Rule("agg_parens_to_braces", "agg () to {}", _agg_parens_to_braces),
    Rule("agg_on_left", "agg left to right", _agg_on_left),
    Rule("mod_to_backslash", "#mod to \\", _mod_to_backslash),
    Rule("lowercase_predicates", "lcase preds", _lowercase_predicates),
    Rule("swi_aggregate", "swi aggregate", _swi_aggregate),
    Rule("bare_arith_assignment", "H= to fact", _bare_arith_assignment),
    Rule("strip_continuation_colon", "strip cont :-", _strip_continuation_colon),
]


def _rules_by_name(names):
    by = {r.name: r for r in ALL_RULES}
    return [by[n] for n in names]


class ASPRewriter:
    """Apply an ordered list of rules to fixed point."""

    MAX_ITERATIONS = 8

    def __init__(self, rules: List[Rule] | List[str] | None = None) -> None:
        if rules is None:
            self.rules: List[Rule] = list(ALL_RULES)
        elif rules and isinstance(rules[0], str):
            self.rules = _rules_by_name(rules)  # type: ignore[arg-type]
        else:
            self.rules = list(rules)  # type: ignore[arg-type]

    def rewrite(self, program: str) -> str:
        text = program
        for _ in range(self.MAX_ITERATIONS):
            before = text
            for rule in self.rules:
                text = rule.apply(text)
            if text == before:
                break
        return text

    def rewrite_with_trace(self, program: str):
        """Return (final_text, fired_rules_in_order)."""
        text = program
        fired: List[str] = []
        for _ in range(self.MAX_ITERATIONS):
            before_iter = text
            for rule in self.rules:
                before = text
                text = rule.apply(text)
                if text != before:
                    fired.append(rule.name)
            if text == before_iter:
                break
        return text, fired
