"""Refinement loop utilities: failure categorization, grid diff, feedback builder."""

import re


def parse_output_atoms(atoms: list[str]) -> dict:
    """Parse output(Row, Col, Color) atoms into {(row, col): color} dict."""
    actual = {}
    for atom in atoms:
        m = re.match(r"^output\((\d+),(\d+),(\d+)\)$", atom)
        if m:
            actual[(int(m.group(1)), int(m.group(2)))] = int(m.group(3))
    return actual


def compute_grid_diff(expected_grid: list, actual_atoms: list[str]) -> list[dict]:
    """Return list of {row, col, expected, got} for deviating cells."""
    expected = {
        (r, c): v for r, row in enumerate(expected_grid) for c, v in enumerate(row)
    }
    actual = parse_output_atoms(actual_atoms)
    diffs = []
    all_keys = set(expected.keys()) | set(actual.keys())
    for r, c in sorted(all_keys):
        exp_val = expected.get((r, c), None)
        got_val = actual.get((r, c), None)
        if exp_val != got_val:
            diffs.append({"row": r, "col": c, "expected": exp_val, "got": got_val})
    return diffs


def categorize_first_failure(pairs: list[dict]) -> tuple:
    """Find the first non-correct pair and return (trigger, pair_index, details).

    trigger is one of: "syntax_error", "unsat", "multi_answer_set", "wrong_cells"

    Returns (None, -1, {}) if all pairs passed.
    """
    for pair in pairs:
        status = pair.get("status", "")
        if status == "correct":
            continue

        idx = pair.get("index", -1)
        if status == "error":
            error_msgs = pair.get("error_messages", ["Unknown Clingo error"])
            return ("syntax_error", idx, {"error_messages": error_msgs})

        if status == "wrong_count(0)":
            return (
                "unsat",
                idx,
                {"message": "No answer sets (UNSAT) — program is over-constrained."},
            )

        if status.startswith("wrong_count("):
            count = status.replace("wrong_count(", "").replace(")", "")
            models = pair.get("actual_atoms", [])
            details = {
                "count": int(count),
                "message": f"{count} answer sets (under-constrained).",
                "models": models,
            }
            return ("multi_answer_set", idx, details)

        if status == "wrong_output":
            actual_atoms = pair.get("actual_atoms", [])
            return ("wrong_cells", idx, {"actual_atoms": actual_atoms})

    return (None, -1, {})


def format_numbered_code(code: str) -> str:
    """Add line numbers to code."""
    lines = code.splitlines()
    width = max(4, len(str(len(lines))))
    return "\n".join(f"{i + 1:>{width}}| {line}" for i, line in enumerate(lines))


def format_grid(grid: list, title: str = None) -> str:
    """Format a 2D grid as a compact string."""
    if not grid:
        return "(empty)"
    rows = []
    if title:
        rows.append(f"  {title}:")
    rows.extend("  " + " ".join(str(c) for c in row) for row in grid)
    return "\n".join(rows)


def format_diff_table(expected_grid: list, actual_atoms: list[str]) -> str:
    """Format a human-readable diff table between expected and actual grids."""
    actual = parse_output_atoms(actual_atoms)
    expected = {
        (r, c): v for r, row in enumerate(expected_grid) for c, v in enumerate(row)
    }
    max_r = max(r for r, _ in expected.keys()) if expected else 0
    max_c = max(c for _, c in expected.keys()) if expected else 0

    lines = []
    lines.append(f"  {'Row':>4s} {'Col':>4s} {'Expected':>10s} {'Got':>10s}")
    lines.append(f"  {'-' * 4} {'-' * 4} {'-' * 10} {'-' * 10}")

    n_diffs = 0
    for r in range(max_r + 1):
        for c in range(max_c + 1):
            exp_val = expected.get((r, c), None)
            got_val = actual.get((r, c), None)
            if exp_val != got_val:
                exp_str = str(exp_val) if exp_val is not None else "."
                got_str = str(got_val) if got_val is not None else "."
                lines.append(f"  {r:4d} {c:4d} {exp_str:>10s} {got_str:>10s}")
                n_diffs += 1

    if n_diffs == 0:
        lines.append("  (no differences found — this should not happen)")
    else:
        lines.append(f"  ({n_diffs} cell(s) differ)")

    return "\n".join(lines)


def build_feedback_message(
    asp_code: str,
    trigger: str,
    pair_index: int,
    details: dict,
    input_grid: list = None,
    expected_grid: list = None,
    round_num: int = 1,
) -> str:
    """Build a feedback user message for the refinement conversation turn."""

    CATEGORY_DESCRIPTIONS = {
        "syntax_error": "Syntax / Compilation Error",
        "unsat": "UNSAT — Over-constrained (no answer sets)",
        "multi_answer_set": "Multiple Answer Sets — Under-constrained",
        "wrong_cells": "Wrong Output Cells — Unique answer set but incorrect values",
    }

    lines = [
        "Your ASP program failed validation. Please analyze the error and fix the program.",
        "",
        f"## Failure Category: {CATEGORY_DESCRIPTIONS.get(trigger, trigger)}",
        f"## First Failing Pair: #{pair_index}",
        "",
    ]

    if trigger == "syntax_error":
        lines.append("### Clingo Error Message(s):")
        for msg in details.get("error_messages", ["Unknown error"]):
            lines.append(f"  {msg}")
        lines.append("")

    elif trigger == "unsat":
        lines.append(f"  {details.get('message', 'No answer sets.')}")
        lines.append("")

    elif trigger == "multi_answer_set":
        lines.append(f"  {details.get('message', 'Multiple answer sets.')}")
        models = details.get("models", [])
        if models:
            lines.append(f"  First answer set ({len(models[0])} atoms):")
            lines.append(
                f"    {', '.join(models[0][:20])}{'...' if len(models[0]) > 20 else ''}"
            )
            if len(models) > 1:
                lines.append(f"  Second answer set ({len(models[1])} atoms):")
                lines.append(
                    f"    {', '.join(models[1][:20])}{'...' if len(models[1]) > 20 else ''}"
                )
        lines.append("")

    elif trigger == "wrong_cells":
        if input_grid is not None:
            lines.append("### Input Grid:")
            lines.append(format_grid(input_grid, "Input"))
            lines.append("")
        if expected_grid is not None:
            lines.append("### Expected Output Grid:")
            lines.append(format_grid(expected_grid, "Expected"))
            lines.append("")
            lines.append("### Your Program's Output:")
            actual_atoms = details.get("actual_atoms", [])
            lines.append(f"  {len(actual_atoms)} output/3 atoms")
            lines.append("")
            lines.append("### Grid Diff (deviating cells):")
            lines.append(format_diff_table(expected_grid, actual_atoms))
            lines.append("")

    lines.append("## Your Program (with line numbers):")
    lines.append("```asp")
    lines.append(format_numbered_code(asp_code))
    lines.append("```")
    lines.append("")

    lines.append("## Instructions")
    lines.append("1. Identify the root cause of the failure.")
    lines.append(
        "2. Fix the program so it produces exactly one answer set with correct output/3 atoms for ALL input/output pairs."
    )
    lines.append("3. Provide the corrected ASP program in a **single** ```asp block.")
    lines.append(
        "4. Do NOT change the overall structure unless necessary. Prefer surgical fixes."
    )
    lines.append(
        "5. Verify EACH rule for variable safety (every variable must appear in a positive, non-arithmetic body literal)."
    )

    return "\n".join(lines)


def build_no_block_feedback_message() -> str:
    """Feedback for the NO_BLOCK case: response missing or malformed.

    * Cause is ambiguous from our side
      - The model may have formatted the answer incorrectly, or
      - The model may have exhausted its `max_tokens` budget during reasoning
        before emitting any visible response.
    """
    lines = [
        "Your previous response did not contain a single ```asp ... ``` fenced block.",
        "",
        "This could be due to one of:",
        "  - a formatting issue (the block was missing, mislabelled, or duplicated), or",
        "  - the model's `max_tokens` budget being exhausted during reasoning,",
        "    before the closing fence was emitted.",
        "",
        "## Instructions",
        "1. Provide the corrected ASP program in a **single** ```asp ... ``` block.",
        "2. Keep your reasoning concise so the program fits within the token budget.",
    ]
    return "\n".join(lines)


def build_refinement_messages(
    initial_messages: list[dict],
    conversation_history: list[tuple[str, str]],
    response_text: str,
    feedback_message: str,
) -> list[dict]:
    """Build the full conversation messages for a refinement turn.

    conversation_history is a list of (previous_assistant_response, previous_feedback) tuples.
    """
    messages = list(initial_messages)

    for prev_resp, prev_feedback in conversation_history:
        messages.append({"role": "assistant", "content": prev_resp or "(no response)"})
        messages.append({"role": "user", "content": prev_feedback})

    messages.append({"role": "assistant", "content": response_text or "(no response)"})
    messages.append({"role": "user", "content": feedback_message})

    return messages
