"""Utility functions for the ARC-ASP pipeline."""

import re


# ---------------------------------------------------------------------------
# Code extraction
# ---------------------------------------------------------------------------

def extract_code_blocks(response):
    """Extract content from triple-tick code blocks (```asp, ```prolog, etc.).

    * Returns the first complete code block found.
    * Falls back to content after an unclosed opening fence.
    * Returns the original response unchanged if no fences found.
    """
    match = re.search(r"```[^\n]*\n(.*?)```", response, re.DOTALL)
    if match:
        return match.group(1).strip()

    match = re.search(r"```[^\n]*\n(.*)", response, re.DOTALL)
    if match:
        return match.group(1).strip()

    return response


# ---------------------------------------------------------------------------
# Grid formatting
# ---------------------------------------------------------------------------

def format_grid(grid):
    """Format a 2D list as space-separated digit rows."""
    return "\n".join(" ".join(str(c) for c in row) for row in grid)


def format_examples_for_prompt(examples):
    """Format ARC training examples as a block of labelled diagram pairs."""
    parts = []
    for i, ex in enumerate(examples, start=1):
        inp = format_grid(ex["input"])
        out = format_grid(ex["output"])
        parts.append(
            f"Example #{i}\n"
            f"Input:\n<Diagram>\n{inp}\n</Diagram>\n\n"
            f"Output:\n<Diagram>\n{out}\n</Diagram>"
        )
    return "\n\n".join(parts)


def format_test_for_prompt(test_cases):
    """Format ARC test inputs as labelled diagrams (no output given)."""
    parts = []
    for i, t in enumerate(test_cases, start=1):
        inp = format_grid(t["input"])
        parts.append(f"Test #{i}\nInput:\n<Diagram>\n{inp}\n</Diagram>")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# ASP fact generation
# ---------------------------------------------------------------------------

def grid_to_input_facts(grid):
    """Convert a 2D grid to ASP input(Row, Col, Color) facts."""
    facts = [f"input({r},{c},{color})." for r, row in enumerate(grid) for c, color in enumerate(row)]
    return "\n".join(facts)


# ---------------------------------------------------------------------------
# Answer-set → grid reconstruction
# ---------------------------------------------------------------------------

def answer_set_to_grid(atoms, n_rows, n_cols):
    """Reconstruct a grid from output(Row, Col, Color) atoms.

    Returns a 2D list of ints (or None for missing cells).
    """
    grid = [[None] * n_cols for _ in range(n_rows)]
    pattern = re.compile(r"output\((\d+),(\d+),(\d+)\)")
    for atom in atoms:
        m = pattern.match(atom)
        if m:
            r, c, color = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 0 <= r < n_rows and 0 <= c < n_cols:
                grid[r][c] = color
    return grid


# ---------------------------------------------------------------------------
# Grid diff
# ---------------------------------------------------------------------------

def grid_diff(predicted, expected):
    """Compare two grids and produce a visual diff.

    Returns:
        (diff_str, accuracy)  when shapes match.
        (error_str, 0.0)      when shapes differ or prediction has None cells.
    """
    pr, pc = len(predicted), len(predicted[0]) if predicted else 0
    er, ec = len(expected), len(expected[0]) if expected else 0

    if pr != er or pc != ec:
        return f"Shape mismatch: predicted {pr}x{pc}, expected {er}x{ec}", 0.0

    n_correct = 0
    n_total = 0
    rows = []
    for r in range(er):
        cells = []
        for c in range(ec):
            n_total += 1
            p = predicted[r][c]
            e = expected[r][c]
            if p == e:
                cells.append(str(e))
                n_correct += 1
            else:
                p_str = str(p) if p is not None else "?"
                cells.append(f"{p_str}/{e}")
        rows.append(" ".join(cells))

    accuracy = n_correct / n_total if n_total > 0 else 0.0
    return "\n".join(rows), accuracy
