"""
Snippet-validate the syntax guide in initial_prompt.txt.

Every code snippet in the prompt is a claim about Clingo syntax — either an
explicit "Wrong:" / "Unsafe:" example that should fail, a "Correct:" / "Safe:"
example that should succeed, or a pseudo-syntax template that should be
skipped. We test by parsing+grounding+solving via the python clingo API.
"""

import re
import textwrap
from pathlib import Path
from clingo.control import Control

PROMPT = Path("/home/dlindberg/Projects/asp-arc/src/prompts/initial_prompt.txt")

# Stub facts for fragments that reference predicates we don't define.
HARNESS = """
% --- harness facts injected by validate_snippets.py ---
input(0,0,1). input(0,1,2). input(1,0,3). input(1,1,4).
color(0..9).
row(0..1). col(0..1).
output_cell(0,0). output_cell(0,1). output_cell(1,0). output_cell(1,1).
n_rows(2). n_cols(2).
condition(0,1). condition(1,2).
zero_cell(0,0).
maxC(2). minC(0). rowStart(0). rowEnd(1).
colInclusive(0). colInclusive(1).
colCount(0,1). colCount(1,1).
"""


def run_clingo_str(prog: str):
    """Returns (status, msg). status in {'ok','parse','ground','warn'}."""
    msgs = []

    def _logger(code, message):
        msgs.append(f"{code.name}: {message.strip()}")

    ctl = Control(["1", "--warn=all"], logger=_logger)
    try:
        ctl.add("base", [], prog)
    except RuntimeError as e:
        return "parse", f"{e}"
    try:
        ctl.ground([("base", [])])
    except RuntimeError as e:
        return "ground", f"{e}"
    bad = [
        m for m in msgs
        if "unsafe" in m.lower() or m.startswith("RuntimeError")
    ]
    if bad:
        return "warn", "; ".join(bad)
    # Solve to detect any further runtime errors. clingo 5.6: solve() returns SolveHandle when async, else SolveResult.
    try:
        res = ctl.solve()
        return "ok", f"sat={res.satisfiable}"
    except Exception as e:
        return "solve", f"{e}"


LABEL_RE = re.compile(
    r"^\s*(?P<label>Correct|Wrong|Unsafe|Safe|Syntax|Example|Fact|Rule|Constraint)\s*:\s*(?P<rest>.*)$"
)


def extract_snippets(text: str):
    lines = text.splitlines()
    out = []
    i = 0
    in_section = False
    while i < len(lines):
        line = lines[i]
        if "<clingo_syntax_reference>" in line:
            in_section = True
            i += 1
            continue
        if "</clingo_syntax_reference>" in line:
            in_section = False
            i += 1
            continue
        if not in_section:
            i += 1
            continue
        m = LABEL_RE.match(line)
        if not m:
            i += 1
            continue
        label = m.group("label")
        first = m.group("rest").rstrip()
        body_lines = []
        if first:
            body_lines.append(first)
        j = i + 1
        label_indent = len(line) - len(line.lstrip())
        while j < len(lines):
            nxt = lines[j]
            if not nxt.strip():
                break
            nxt_indent = len(nxt) - len(nxt.lstrip())
            if nxt_indent <= label_indent:
                break
            if LABEL_RE.match(nxt):
                break
            body_lines.append(nxt.rstrip())
            j += 1
        code = "\n".join(body_lines).strip()
        if code:
            out.append({"line": i + 1, "label": label, "code": code})
        i = j
    return out


def expected_outcome(label: str) -> str:
    if label in ("Wrong", "Unsafe"):
        return "fail"
    if label in ("Correct", "Safe", "Example", "WorkedExample"):
        return "pass"
    # "Syntax" / "Fact" / "Rule" / "Constraint" are pseudo-syntax templates ("pred(t1,...,tn).")
    return "skip"


def is_pseudo(code: str) -> bool:
    """Detect template snippets that contain literal placeholders like t1,...,tn or ellipsis."""
    if "..." in code:
        return True
    # Single-token placeholders typical for templates: 'l { atom : condition } u', 'L0 : L1, ..., Ln'
    if re.search(r"\bL\d+\b", code):
        return True
    if re.search(r"\bt\d+\b", code):
        return True
    if re.fullmatch(r"\s*l\s*\{[^}]*\}\s*u\s*", code):
        return True
    if re.fullmatch(r"\s*head\s*:-\s*body\s*\.?\s*", code):
        return True
    if re.fullmatch(r"\s*:-\s*body\s*\.?\s*", code):
        return True
    if re.fullmatch(r"\s*pred\([^)]*\)\.?\s*", code):
        return True
    return False


def is_full_program(code: str) -> bool:
    return "#show" in code


def test_snippet(snippet) -> dict:
    code = snippet["code"]
    # Strip lines that are only comments
    visible = [ln for ln in code.splitlines() if ln.strip() and not ln.strip().startswith("%")]
    if not visible:
        return {**snippet, "skipped": True, "reason": "comment-only"}
    if is_pseudo(code):
        return {**snippet, "skipped": True, "reason": "pseudo-syntax template"}

    if is_full_program(code):
        prog = code
    else:
        prog = HARNESS + "\n" + code + "\n"

    status, msg = run_clingo_str(prog)
    ok = status == "ok"
    expected = expected_outcome(snippet["label"])
    if expected == "pass":
        agree = ok
    elif expected == "fail":
        agree = not ok
    else:
        agree = None
    return {
        **snippet,
        "skipped": False,
        "status": status,
        "ok": ok,
        "msg": msg,
        "expected": expected,
        "agree": agree,
    }


def extract_worked_example(text: str):
    """Pull the <asp>...</asp> block from the worked example."""
    m = re.search(r"<asp>(.*?)</asp>", text, flags=re.DOTALL)
    if not m:
        return None
    code = m.group(1).strip()
    return {"line": text[: m.start()].count("\n") + 1, "label": "WorkedExample", "code": code}


def main():
    text = PROMPT.read_text()
    snippets = extract_snippets(text)

    # Add the worked example (simulate input/3 + color/1 from the harness so it grounds)
    worked = extract_worked_example(text)
    if worked:
        snippets.append(worked)
    print(f"Extracted {len(snippets)} snippets from {PROMPT}\n")

    results = [test_snippet(s) for s in snippets]

    print(f"{'#':>3}  {'line':>4}  {'label':<10}  {'exp':<5}  {'got':<7}  agree")
    print("-" * 80)
    disagreements = []
    skipped = 0
    for n, r in enumerate(results, 1):
        if r.get("skipped"):
            print(f"{n:>3}  {r['line']:>4}  {r['label']:<10}  -      skip     ({r['reason']})")
            skipped += 1
            continue
        got = "pass" if r["ok"] else f"fail/{r['status']}"
        flag = "OK" if r["agree"] else "MISMATCH"
        print(f"{n:>3}  {r['line']:>4}  {r['label']:<10}  {r['expected']:<5}  {got:<7}  {flag}")
        if not r["agree"]:
            disagreements.append((n, r))

    print("\n" + "=" * 80)
    print(f"{len(snippets)} extracted; {skipped} skipped; {len(disagreements)} disagreements")
    print("=" * 80)
    for n, r in disagreements:
        print(f"\n[#{n}, line {r['line']}, {r['label']}: expected {r['expected']}, got {'pass' if r['ok'] else r['status']}]")
        print(textwrap.indent(r["code"], "    "))
        print(f"  msg: {r['msg'][:400]}")


if __name__ == "__main__":
    main()
