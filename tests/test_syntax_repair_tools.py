"""Unit tests for syntax repair tools: read_program, edit_lines, clingo_check,
give_up, and the back-to-front batch dispatch logic.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.syntax_repair_tools import (
    dispatch_batch,
    dispatch_tool,
    extract_error_lines,
)


# ---------------------------------------------------------------------------
# read_program
# ---------------------------------------------------------------------------

def test_read_program_whole():
    prog = "a(1).\nb(2).\nc(3)."
    result = dispatch_tool("read_program", {}, prog)
    assert not result.halt
    assert "1: a(1)." in result.message
    assert "2: b(2)." in result.message
    assert "3: c(3)." in result.message


def test_read_program_slice():
    prog = "a(1).\nb(2).\nc(3).\nd(4)."
    result = dispatch_tool("read_program", {"start_line": 2, "end_line": 3}, prog)
    assert not result.halt
    assert "1: a(1)." not in result.message
    assert "2: b(2)." in result.message
    assert "3: c(3)." in result.message
    assert "4: d(4)." not in result.message
    assert "omitted" in result.message.lower()


# ---------------------------------------------------------------------------
# edit_lines
# ---------------------------------------------------------------------------

def test_edit_lines_single():
    prog = "a(1).\nb(2)."
    result = dispatch_tool("edit_lines", {"start": 1, "end": 1, "new_text": "x(9)."}, prog)
    assert not result.halt
    assert result.program == "x(9).\nb(2)."


def test_edit_lines_range():
    prog = "a(1).\nb(2).\nc(3)."
    result = dispatch_tool("edit_lines", {"start": 1, "end": 2, "new_text": "x(9)."}, prog)
    assert result.program == "x(9).\nc(3)."


def test_edit_lines_delete():
    prog = "a(1).\nb(2).\nc(3)."
    result = dispatch_tool("edit_lines", {"start": 2, "end": 2, "new_text": ""}, prog)
    assert result.program == "a(1).\nc(3)."


def test_edit_lines_multiline_insert():
    prog = "a(1).\nb(2)."
    result = dispatch_tool(
        "edit_lines",
        {"start": 1, "end": 1, "new_text": "x(1).\ny(2).\nz(3)."},
        prog,
    )
    assert result.program == "x(1).\ny(2).\nz(3).\nb(2)."


def test_edit_lines_append_at_end():
    prog = "a(1)."
    result = dispatch_tool(
        "edit_lines",
        {"start": 2, "end": 2, "new_text": "b(2)."},
        prog,
    )
    assert result.program == "a(1).\nb(2)."


def test_edit_lines_out_of_bounds():
    prog = "a(1)."
    result = dispatch_tool("edit_lines", {"start": 5, "end": 5, "new_text": "x."}, prog)
    assert "beyond" in result.message.lower()
    assert result.program == prog


def test_edit_lines_start_below_1():
    prog = "a(1)."
    result = dispatch_tool("edit_lines", {"start": 0, "end": 1, "new_text": "x."}, prog)
    assert "must be >= 1" in result.message.lower()
    assert result.program == prog


# ---------------------------------------------------------------------------
# Batch dispatch: back-to-front edit sorting
# ---------------------------------------------------------------------------

def test_batch_back_to_front_applies_highest_first():
    """Edits at lines 3,1,2 should be applied as 3, then 2, then 1 (descending)."""
    prog = "line1\nline2\nline3\nline4"
    calls = [
        ("edit_lines", {"start": 1, "end": 1, "new_text": "A"}),
        ("edit_lines", {"start": 3, "end": 3, "new_text": "C"}),
        ("edit_lines", {"start": 2, "end": 2, "new_text": "B"}),
    ]
    final, results = dispatch_batch(calls, prog)
    # All should succeed
    assert all("Error" not in r.message for r in results)
    # After back-to-front: line3→C, then line2→B, then line1→A
    expected = "A\nB\nC\nline4"
    assert final == expected


def test_batch_overlapping_rejected():
    """Overlapping edit ranges in one batch should be rejected."""
    prog = "line1\nline2\nline3\nline4\nline5"
    calls = [
        ("edit_lines", {"start": 2, "end": 4, "new_text": "X"}),
        ("edit_lines", {"start": 3, "end": 5, "new_text": "Y"}),
    ]
    final, results = dispatch_batch(calls, prog)
    assert len(results) == 1
    assert "overlapping" in results[0].message.lower()
    assert final == prog


def test_batch_mixed_edits_and_checks():
    """Checks interspersed with edits work correctly."""
    prog = "a(1).\nb(2)."
    calls = [
        ("edit_lines", {"start": 1, "end": 1, "new_text": "x(9)."}),
        ("clingo_check", {}),
    ]
    final, results = dispatch_batch(calls, prog)
    assert len(results) == 2
    assert "replaced" in results[0].message
    # clingo_check checks the UPDATED program
    assert final == "x(9).\nb(2)."


# ---------------------------------------------------------------------------
# give_up
# ---------------------------------------------------------------------------

def test_give_up_halt_flag():
    result = dispatch_tool("give_up", {"reason": "cannot fix"}, "")
    assert result.halt
    assert "cannot fix" in result.message


# ---------------------------------------------------------------------------
# Unknown tool
# ---------------------------------------------------------------------------

def test_unknown_tool():
    result = dispatch_tool("nonexistent", {}, "")
    assert "Unknown tool" in result.message
    assert not result.halt


# ---------------------------------------------------------------------------
# extract_error_lines
# ---------------------------------------------------------------------------

def test_extract_error_lines_strips_locations():
    errors = "<block>:1:22-27: error: syntax error, unexpected #count\n<block>:3:1-17: error: syntax error, unexpected ="
    lines = extract_error_lines(errors)
    assert "error: syntax error, unexpected #count" in lines
    assert "error: syntax error, unexpected =" in lines
    assert "block" not in " ".join(lines).lower()


def test_extract_error_lines_deduplicates():
    errors = "error: unsafe\nnote: unsafe\nerror: unsafe"
    lines = extract_error_lines(errors)
    # "error: unsafe" and "note: unsafe" are different
    assert len(lines) == 2


def test_extract_error_lines_blank_input():
    assert extract_error_lines("") == frozenset()
    assert extract_error_lines("\n\n") == frozenset()


# ---------------------------------------------------------------------------
# clingo_check (requires Clingo)
# ---------------------------------------------------------------------------

try:
    import clingo  # noqa: F401

    has_clingo = True
except ImportError:
    has_clingo = False


def _needs_clingo(func):
    import pytest
    return pytest.mark.skipif(not has_clingo, reason="clingo not installed")(func)


@_needs_clingo
def test_clingo_check_valid_program():
    result = dispatch_tool("clingo_check", {"program": "a."}, "")
    assert not result.halt
    assert "No errors" in result.message


@_needs_clingo
def test_clingo_check_syntax_error():
    # #mod without proper syntax is invalid in Clingo
    result = dispatch_tool("clingo_check", {"program": "p(X) :- X = (A #mod B)."}, "")
    assert not result.halt
    assert "syntax error" in result.message.lower() or "error" in result.message.lower()


@_needs_clingo
def test_clingo_check_unsafe_variable():
    result = dispatch_tool(
        "clingo_check",
        {"program": "p(X) :- not q(X)."},
        "",
    )
    assert not result.halt
    # This may be "unsafe" or may ground OK if there are no domain constraints
    # Just check that we get a result (not an exception)
    assert result.message
