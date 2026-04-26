"""The 4 tools for the syntax-repair agent: read_program, edit_lines,
clingo_check, give_up.

Each tool operates on a shared program string mutated in place.  The
dispatcher is stateless — it receives the current program and returns
the updated program plus a result message.
"""

from __future__ import annotations

from dataclasses import dataclass

from utils.clingo import run_clingo


# ---------------------------------------------------------------------------
# Public API: dispatch a single tool call
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    message: str       # human-readable result for the model
    program: str       # updated program (same as input if tool didn't mutate)
    halt: bool         # True when the agent signals give_up


def dispatch_tool(name: str, args: dict, program: str) -> ToolResult:
    if name == "read_program":
        return _read_program(args, program)
    elif name == "edit_lines":
        return _edit_lines(args, program)
    elif name == "clingo_check":
        return _clingo_check(args, program)
    elif name == "give_up":
        return _give_up(args, program)
    else:
        return ToolResult(
            message=f"Unknown tool '{name}'. Available: read_program, edit_lines, clingo_check, give_up.",
            program=program,
            halt=False,
        )


def dispatch_batch(calls: list[tuple[str, dict]], program: str) -> tuple[str, list[ToolResult]]:
    """Dispatch several tool calls sequentially against the same program.
    Returns (final_program, [result_for_call_0, result_for_call_1, ...]).

    edit_lines calls are sorted back-to-front (descending start) before
    application so that earlier edits don't shift later line numbers.
    Overlapping ranges within the batch are rejected.
    """
    num_calls = len(calls)

    # Sort edit_lines calls by descending start line for back-to-front application.
    # Non-edit calls stay in their relative order but interleave correctly.
    indexed: list[tuple[int, str, dict]] = [(i, n, a) for i, (n, a) in enumerate(calls)]

    edit_indices: list[tuple[int, int, int, str]] = []  # (orig_idx, start, end, new_text)
    non_edit: list[tuple[int, str, dict]] = []
    for orig_idx, name, args in indexed:
        if name == "edit_lines":
            start = int(args.get("start", 1))
            end = int(args.get("end", start))
            new_text = str(args.get("new_text", ""))
            edit_indices.append((orig_idx, start, end, new_text))
        else:
            non_edit.append((orig_idx, name, args))

    # Sort edits back-to-front
    edit_indices.sort(key=lambda x: x[1], reverse=True)

    # Check for overlapping ranges
    for i in range(len(edit_indices)):
        for j in range(i + 1, len(edit_indices)):
            _, s1, e1, _ = edit_indices[i]
            _, s2, e2, _ = edit_indices[j]
            # Descending sort: s1 >= s2. Overlap if s1 <= e2 (higher-range
            # start falls within lower-range extent).
            if s1 <= e2:
                return program, [
                    ToolResult(
                        message=(
                            f"Error: overlapping edit_lines ranges in batch: "
                            f"lines {s1}-{e1} and lines {s2}-{e2}. "
                            f"Split these edits across separate turns."
                        ),
                        program=program,
                        halt=False,
                    )
                ]
    if sum(1 for _, n, _ in non_edit if n == "edit_lines"):
        pass  # handled above

    # Reconstruct execution order: all edits (in back-to-front order) + non-edits
    # interspersed by original index.
    # Simpler: execute edits first (back-to-front), then non-edits.
    # But the doc says "later calls see effects of earlier ones", so we should
    # respect original order where possible. However, the back-to-front sorting
    # overrides edit order to prevent line-number shifting.
    #
    # Strategy: execute all edit_lines first (back-to-front), then execute
    # non-edit calls in original order. This mirrors "batch edits, then check".
    results_map: dict[int, ToolResult] = {}

    # Collect the batch's edits (back-to-front)
    if edit_indices:
        # Validate and pre-check all edits before applying any
        lines = program.split("\n")
        n_lines = len(lines)
        for _, start, end, new_text in edit_indices:
            if start < 1:
                return program, [
                    ToolResult(
                        message=f"Error: start line {start} must be >= 1.",
                        program=program,
                        halt=False,
                    )
                ]
            if end < start:
                return program, [
                    ToolResult(
                        message=f"Error: end line {end} < start line {start}.",
                        program=program,
                        halt=False,
                    )
                ]
            if start > n_lines + 1:
                return program, [
                    ToolResult(
                        message=f"Error: start line {start} is beyond program (has {n_lines} lines).",
                        program=program,
                        halt=False,
                    )
                ]

    prog = program
    for orig_idx, start, end, new_text in edit_indices:
        result = _edit_lines({"start": start, "end": end, "new_text": new_text}, prog)
        prog = result.program
        results_map[orig_idx] = result

    # Execute non-edit calls in original order
    for orig_idx, name, args in non_edit:
        result = dispatch_tool(name, args, prog)
        prog = result.program
        results_map[orig_idx] = result

    # Build result list in original order
    results = [results_map[i] for i in range(num_calls)]

    return prog, results


# ---------------------------------------------------------------------------
# Individual tool implementations
# ---------------------------------------------------------------------------

def _read_program(args: dict, program: str) -> ToolResult:
    start = int(args.get("start_line", 1))
    end = int(args.get("end_line", -1))
    lines = program.split("\n")
    n = len(lines)

    if start < 1:
        start = 1
    if end < 1 or end > n:
        end = n
    if start > n:
        start = n
    if end < start:
        end = start

    snippet = lines[start - 1 : end]
    numbered = "\n".join(f"{i}: {line}" for i, line in enumerate(snippet, start=start))

    msg = f"Program lines {start}-{end} of {n}:\n{numbered}"
    if n > end:
        msg += f"\n... (lines {end + 1}-{n} omitted, {n - end} more lines)"
    if start > 1:
        header = f"({start - 1} lines before line {start} omitted)\n"
        msg = header + msg

    return ToolResult(message=msg, program=program, halt=False)


def _edit_lines(args: dict, program: str) -> ToolResult:
    start = int(args.get("start", 1))
    end = int(args.get("end", start))
    new_text = str(args.get("new_text", ""))

    lines = program.split("\n")
    n = len(lines)

    if start < 1:
        return ToolResult(
            message=f"Error: start line {start} must be >= 1.",
            program=program,
            halt=False,
        )
    if end < start:
        return ToolResult(
            message=f"Error: end line {end} < start line {start}.",
            program=program,
            halt=False,
        )
    if start > n + 1:
        return ToolResult(
            message=f"Error: start line {start} is beyond program (has {n} lines, valid range: 1-{n + 1}).",
            program=program,
            halt=False,
        )

    # Convert to 0-indexed
    s0 = start - 1
    e0 = min(end - 1, n - 1)

    if new_text == "":
        # Delete lines
        new_lines = lines[:s0] + lines[e0 + 1 :]
    else:
        new_lines = lines[:s0] + new_text.split("\n") + lines[e0 + 1 :]

    new_program = "\n".join(new_lines)

    old_range = f"{start}-{end}" if start != end else str(start)
    return ToolResult(
        message=f"edit_lines: Lines {old_range} replaced. Program now has {len(new_lines)} lines.",
        program=new_program,
        halt=False,
    )


def _clingo_check(args: dict, program: str) -> ToolResult:
    prog = str(args.get("program", "")).strip()
    if not prog:
        prog = program

    err_type, clingo_messages = run_clingo(prog)

    if err_type is None:
        return ToolResult(
            message="clingo_check: No errors (grounding succeeded).",
            program=program,
            halt=False,
        )
    else:
        if not clingo_messages:
            msg = "clingo_check: Grounding failed (timeout or internal error)."
        else:
            lines = []
            for _code, message in clingo_messages:
                lines.append(message)
            msg = "clingo_check errors:\n" + "\n".join(lines)
        return ToolResult(message=msg, program=program, halt=False)


def _give_up(args: dict, program: str) -> ToolResult:
    reason = str(args.get("reason", "unspecified"))
    return ToolResult(
        message=f"give_up acknowledged. Reason: {reason}",
        program=program,
        halt=True,
    )


# ---------------------------------------------------------------------------
# Error extraction helpers (used by agent, not tools)
# ---------------------------------------------------------------------------

def extract_error_lines(clingo_errors_text: str) -> frozenset[str]:
    """Parse Clingo error output into a stable set of error-message lines.
    Strips leading <block>:line:col-range: prefixes to make errors
    comparable across rounds (line numbers shift after edits).
    """
    import re

    lines = set()
    for line in clingo_errors_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Strip location prefix: <block>:27:45-46: error: ...
        stripped = re.sub(r"^<block>:\d+(:\d+(-\d+)?)?:\s*", "", line)
        stripped = re.sub(r"^<block>:\d+:\d+-\d+:\s*", "", stripped)
        if stripped:
            lines.add(stripped)
    return frozenset(lines)
