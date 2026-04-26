"""Per-rule unit tests with hand-picked (before, after) and (unchanged)
fixtures.  Each rule has its own block; tests do not depend on Clingo."""

from __future__ import annotations

import sys
from pathlib import Path

# Make `import src.utils.rewriter` work without an editable install.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.rewriter import (  # noqa: E402
    ASPRewriter,
    _agg_on_left,
    _agg_parens_to_braces,
    _bare_arith_assignment,
    _lowercase_predicates,
    _mod_to_backslash,
    _strip_continuation_colon,
    _strip_fences,
    _strip_html_tags,
    _swi_aggregate,
)


def _norm(s: str) -> str:
    return "\n".join(line.rstrip() for line in s.strip().splitlines())


# ---------------------------------------------------------------------------
# strip_fences
# ---------------------------------------------------------------------------


def test_strip_fences_removes_markdown_block():
    src = "Here is the program:\n```asp\nfoo(X) :- bar(X).\n```\nDone."
    assert _norm(_strip_fences(src)) == "foo(X) :- bar(X)."


def test_strip_fences_removes_xml_wrappers():
    src = "<asp>\nfoo(1).\n</asp>"
    assert "asp" not in _strip_fences(src).lower() or "<" not in _strip_fences(src)


def test_strip_fences_unchanged_on_clean_program():
    src = "foo(X) :- bar(X)."
    assert _strip_fences(src).strip() == src


def test_strip_fences_drops_leading_prose():
    src = (
        "Looking at the examples, this is the rule.\n"
        "It is interesting!\n"
        "foo(1)."
    )
    out = _strip_fences(src).strip()
    assert out.startswith("foo(1)")


# ---------------------------------------------------------------------------
# strip_html_tags
# ---------------------------------------------------------------------------


def test_strip_html_tags_handles_open_only():
    assert _strip_html_tags("<asp>\nfoo.\n").strip() == "foo."


def test_strip_html_tags_unchanged():
    assert _strip_html_tags("foo(X) :- bar(X).") == "foo(X) :- bar(X)."


# ---------------------------------------------------------------------------
# agg_parens_to_braces
# ---------------------------------------------------------------------------


def test_agg_parens_simple():
    src = "n(N) :- N = #count(R : input(R, _, _))."
    assert _agg_parens_to_braces(src) == "n(N) :- N = #count{R : input(R, _, _)}."


def test_agg_parens_unchanged_when_already_braces():
    src = "n(N) :- N = #count{R : input(R, _, _)}."
    assert _agg_parens_to_braces(src) == src


# ---------------------------------------------------------------------------
# agg_on_left
# ---------------------------------------------------------------------------


def test_agg_on_left_eq():
    src = "p :- #count{X : q(X)} = 3."
    assert _agg_on_left(src) == "p :- 3 = #count{X : q(X)}."


def test_agg_on_left_geq_flips():
    src = "p :- #count{X : q(X)} >= 3."
    assert _agg_on_left(src) == "p :- 3 <= #count{X : q(X)}."


def test_agg_on_left_with_var_rhs():
    src = "p(N) :- #count{X : q(X)} = N."
    assert _agg_on_left(src) == "p(N) :- N = #count{X : q(X)}."


def test_agg_on_left_unchanged_when_already_right():
    src = "p(N) :- N = #count{X : q(X)}."
    assert _agg_on_left(src) == src


# ---------------------------------------------------------------------------
# mod_to_backslash
# ---------------------------------------------------------------------------


def test_mod_to_backslash():
    assert _mod_to_backslash("X = (A #mod B)") == "X = (A \\ B)"


def test_mod_unchanged():
    assert _mod_to_backslash("X = A \\ B") == "X = A \\ B"


# ---------------------------------------------------------------------------
# lowercase_predicates
# ---------------------------------------------------------------------------


def test_lowercase_predicate_head():
    src = "BlockColor(R, C, V) :- input(R, C, V)."
    assert (
        _lowercase_predicates(src)
        == "blockColor(R, C, V) :- input(R, C, V)."
    )


def test_lowercase_predicate_in_body():
    src = "p(X) :- BlockColor(_, _, X)."
    assert _lowercase_predicates(src) == "p(X) :- blockColor(_, _, X)."


def test_lowercase_predicate_leaves_variables_alone():
    src = "p(X) :- input(X, Y, V), X != Y."
    assert _lowercase_predicates(src) == src


# ---------------------------------------------------------------------------
# swi_aggregate
# ---------------------------------------------------------------------------


def test_swi_aggregate_count():
    src = "p(N) :- aggregate_all(count, q(X), N)."
    out = _swi_aggregate(src)
    assert out == "p(N) :- N = #count{1, q(X) : q(X)}."


def test_swi_aggregate_unchanged():
    src = "p(N) :- N = #count{X : q(X)}."
    assert _swi_aggregate(src) == src


# ---------------------------------------------------------------------------
# bare_arith_assignment
# ---------------------------------------------------------------------------


def test_bare_arith_assignment():
    src = "H = maxRow - minRow + 1."
    assert _bare_arith_assignment(src) == "h(H) :- H = maxRow - minRow + 1."


def test_bare_arith_assignment_unchanged_for_lowercase():
    src = "h(H) :- H = 1."
    assert _bare_arith_assignment(src) == src


# ---------------------------------------------------------------------------
# strip_continuation_colon
# ---------------------------------------------------------------------------


def test_strip_continuation_colon():
    src = "p(X) :- q(X),\n    :- r(X)."
    assert _strip_continuation_colon(src) == "p(X) :- q(X),\n    r(X)."


def test_strip_continuation_colon_unchanged_real_constraint():
    src = "p(X) :- q(X).\n:- r(X)."
    assert _strip_continuation_colon(src) == src


# ---------------------------------------------------------------------------
# end-to-end fixed point
# ---------------------------------------------------------------------------


def test_rewriter_idempotent_on_clean_program():
    src = "foo(X) :- bar(X).\n"
    rw = ASPRewriter()
    assert rw.rewrite(src).strip() == src.strip()


def test_rewriter_combined_fixes():
    src = (
        "Here is the program:\n"
        "```asp\n"
        "p(N) :- #count{X : q(X)} = N.\n"
        "BlockColor(C) :- p(C).\n"
        "```\n"
    )
    out = ASPRewriter().rewrite(src)
    assert "```" not in out
    assert "BlockColor" not in out
    assert "N = #count" in out


def test_empty_rule_list_is_noop():
    src = "Looking at this:\n```asp\nfoo.\n```"
    assert ASPRewriter(rules=[]).rewrite(src) == src
