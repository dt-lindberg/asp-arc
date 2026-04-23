"""
Streamlit inspector for asp-arc pipeline runs.

* Sidebar: run file selector + puzzle list
* Main area: puzzle grids, step-by-step reasoning, generated program,
  per-example verification results, and refinement history

Usage (from project root):
    streamlit run streamlit-interface/app.py
"""

import glob
import json
import os
import re
import sys

import streamlit as st

# src/ on path so utils, config etc. are importable
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

st.set_page_config(page_title="ARC-ASP Inspector", layout="wide")

# ---------------------------------------------------------------------------
# ARC colour palette (standard across all ARC-AGI tasks)
# ---------------------------------------------------------------------------

ARC_COLORS = {
    0: "#000000",  # black
    1: "#0074D9",  # blue
    2: "#FF4136",  # red
    3: "#2ECC40",  # green
    4: "#FFDC00",  # yellow
    5: "#AAAAAA",  # grey
    6: "#F012BE",  # fuchsia
    7: "#FF851B",  # orange
    8: "#7FDBFF",  # azure
    9: "#870C25",  # maroon
}

# ---------------------------------------------------------------------------
# Grid rendering
# ---------------------------------------------------------------------------


def _grid_html(grid, cell_px=26):
    """Render a 2D integer grid as a coloured HTML table."""
    rows = []
    for row in grid:
        cells = []
        for val in row:
            color = ARC_COLORS.get(val, "#333333") if val is not None else "#333333"
            cells.append(
                f'<td style="width:{cell_px}px;height:{cell_px}px;'
                f'background:{color};border:1px solid #111;"></td>'
            )
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return (
        '<table style="border-collapse:collapse;display:inline-block">'
        + "".join(rows)
        + "</table>"
    )


def show_example_navigator(puzzle_idx, train_examples, test_examples, train_verifications):
    """Show one example at a time with prev/next navigation.

    Covers all training examples followed by test instances. For train examples,
    the predicted grid from verification results is shown if available.
    """
    # Build flat list of slides: each is a dict with type, label, input, expected, predicted
    slides = []
    verif_by_idx = {v["example_idx"]: v for v in train_verifications}

    for i, ex in enumerate(train_examples):
        v = verif_by_idx.get(i, {})
        slides.append({
            "label": f"Train {i + 1} / {len(train_examples)}",
            "input": ex["input"],
            "expected": ex["output"],
            "predicted": v.get("grid_predicted"),
            "correct": v.get("correct"),
        })

    for i, ex in enumerate(test_examples):
        slides.append({
            "label": f"Test {i + 1}" + (" / " + str(len(test_examples)) if len(test_examples) > 1 else ""),
            "input": ex["input"],
            "expected": ex.get("output"),
            "predicted": None,
            "correct": None,
        })

    n = len(slides)
    if n == 0:
        st.caption("No examples available.")
        return

    state_key = f"p{puzzle_idx}_ex_idx"
    if state_key not in st.session_state:
        st.session_state[state_key] = 0
    # Clamp in case puzzle count changed
    st.session_state[state_key] = max(0, min(st.session_state[state_key], n - 1))
    idx = st.session_state[state_key]
    slide = slides[idx]

    # Navigation bar
    nav_left, nav_mid, nav_right = st.columns([1, 4, 1])
    with nav_left:
        if st.button("← Prev", key=f"p{puzzle_idx}_prev", disabled=idx == 0):
            st.session_state[state_key] -= 1
            st.rerun()
    with nav_mid:
        st.markdown(
            f'<div style="text-align:center;font-size:13px;color:#aaa;padding-top:6px">'
            f'{slide["label"]}</div>',
            unsafe_allow_html=True,
        )
    with nav_right:
        if st.button("Next →", key=f"p{puzzle_idx}_next", disabled=idx == n - 1):
            st.session_state[state_key] += 1
            st.rerun()

    # Grid columns: always show input + expected; add predicted if present
    has_predicted = slide["predicted"] is not None
    has_expected = slide["expected"] is not None

    if has_predicted:
        cols = st.columns(3, gap="medium")
        labels = ["Input", "Expected", "Predicted"]
        grids = [slide["input"], slide["expected"], slide["predicted"]]
    elif has_expected:
        cols = st.columns(2, gap="medium")
        labels = ["Input", "Expected"]
        grids = [slide["input"], slide["expected"]]
    else:
        cols = st.columns(1)
        labels = ["Input"]
        grids = [slide["input"]]

    for col, label, grid in zip(cols, labels, grids):
        with col:
            st.caption(label)
            st.html(_grid_html(grid))

    # Pass/fail badge for train examples (idx is the slide index = train example index)
    if slide["correct"] is not None:
        if slide["correct"]:
            st.success("Correct", icon="✓")
        else:
            v = verif_by_idx.get(idx, {})
            status = v.get("status", "wrong").replace("_", " ").upper()
            if v.get("clingo_errors"):
                st.error(f"{status}\n\n```\n{v['clingo_errors']}\n```")
            elif v.get("diff"):
                st.error(status)
                st.text(v["diff"])


def show_verification_grids(verifications, examples):
    """Show per-example verification result alongside the expected and predicted grids."""
    for v in verifications:
        idx = v["example_idx"]
        status = v["status"]
        correct = v["correct"]
        acc = v.get("accuracy", 0.0)

        badge_color = "#2ECC40" if correct else "#FF4136"
        badge_text = "PASS" if correct else status.upper().replace("_", " ")

        st.html(
            f'<div style="margin:6px 0 2px">'
            f'<span style="font-size:13px;font-weight:600">Example {idx + 1}</span> '
            f'<span style="background:{badge_color};color:#fff;border-radius:3px;'
            f'padding:1px 7px;font-size:11px">{badge_text}</span>'
            + (f' <span style="font-size:11px;color:#aaa">acc={acc:.2f}</span>' if not correct and acc else "")
            + "</div>"
        )

        cols = st.columns([1, 1, 1], gap="small")
        with cols[0]:
            st.caption("Input")
            if idx < len(examples):
                st.html(_grid_html(examples[idx]["input"]))
            else:
                st.caption("(input not available)")

        with cols[1]:
            st.caption("Expected output")
            if v.get("grid_expected"):
                st.html(_grid_html(v["grid_expected"]))

        with cols[2]:
            st.caption("Predicted output")
            if v.get("grid_predicted"):
                st.html(_grid_html(v["grid_predicted"]))
            elif correct is False and v.get("grid_predicted") is None:
                st.caption("(no prediction)")

        if not correct:
            if v.get("diff"):
                st.text(v["diff"])
            if v.get("clingo_errors"):
                st.code(v["clingo_errors"], language=None)


# ---------------------------------------------------------------------------
# Code block with error line highlighting
# ---------------------------------------------------------------------------

_CODE_DIV = (
    "display:flex;justify-content:space-between;align-items:center;"
    "white-space:pre;font-family:monospace;font-size:12px;line-height:1.5;"
    "padding:0 8px;"
)


def _error_lines_from_str(errors_str):
    """Extract error line numbers from Clingo error messages like '<block>:N:'."""
    return {int(m) for m in re.findall(r"<block>:(\d+):", errors_str)}


def show_program(code, errors_str=""):
    """Render an ASP program with error lines highlighted in red."""
    error_lines = _error_lines_from_str(errors_str) if errors_str else set()
    rows = []
    for line_num, line in enumerate(code.splitlines(), start=1):
        escaped = (
            line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        bg = "rgba(255,80,80,0.18)" if line_num in error_lines else "transparent"
        warn = (
            '<span style="color:#ffaa00;user-select:none">&#9888;</span>'
            if line_num in error_lines
            else "<span></span>"
        )
        rows.append(
            f'<div style="background:{bg};{_CODE_DIV}">'
            f'<span>'
            f'<span style="color:#555;user-select:none;margin-right:12px">{line_num:>3}</span>'
            f'{escaped}'
            f'</span>'
            f"{warn}</div>"
        )
    st.html(
        '<div style="overflow-x:auto;background:#1a1a1a;border-radius:6px;'
        'border:1px solid #333;padding:6px 0">'
        + "".join(rows)
        + "</div>"
    )


# ---------------------------------------------------------------------------
# Generation step display
# ---------------------------------------------------------------------------

# Ordered list of (field_name, kind, label, text_area_height).
# - kind "prose": wrapped in an expander, rendered via st.text_area
# - kind "code":  rendered inline via show_program with error-line highlighting
# Unknown/extra fields on a step are ignored gracefully, so the schema can grow
# over time without breaking the viewer.
STEP_FIELD_SPEC = [
    ("prompt",    "prose", "Prompt",    240),
    ("thinking",  "prose", "Thinking",  320),
    ("response",  "prose", "Response",  240),
    ("extracted", "code",  "Extracted", None),
    ("program",   "code",  "Program",   None),
]

# Fields that carry structural/metadata information rather than free-form text.
# Listed here so they don't get rendered as generic key/value pairs.
STEP_META_FIELDS = {"attempt", "all_train_correct", "train_verifications"}


def show_step_fields(step, key_prefix, puzzle_idx, error_lines_str=""):
    """Render the known text/code fields of a step in a consistent order.

    * Prose fields (prompt/thinking/response) go into expanders.
      - Code fields (extracted/program) are rendered with error-line highlighting.
    """
    for field, kind, label, height in STEP_FIELD_SPEC:
        val = step.get(field)
        if not (isinstance(val, str) and val.strip()):
            continue
        if kind == "prose":
            with st.expander(label, expanded=False):
                st.text_area(
                    label,
                    value=val,
                    height=height,
                    disabled=True,
                    label_visibility="collapsed",
                    key=f"p{puzzle_idx}_{key_prefix}_{field}",
                )
        else:
            st.markdown(f"**{label}**")
            show_program(val, error_lines_str)


def show_step(step, title, key_prefix, examples, puzzle_idx, expanded=False):
    """Render one pipeline step (initial or refinement) as a collapsible section.

    * Title bar summarises solve status based on all_train_correct if present.
      - Body shows the step's text/code fields followed by per-example verification grids.
    """
    verifications = step.get("train_verifications", []) or []
    # Aggregate any clingo errors so show_program can highlight offending lines
    all_errors = " ".join(v.get("clingo_errors", "") or "" for v in verifications)

    all_ok = step.get("all_train_correct")
    if all_ok is True:
        status = " — SOLVED"
    elif all_ok is False:
        status = " — UNSOLVED"
    else:
        status = ""

    with st.expander(f"{title}{status}", expanded=expanded):
        show_step_fields(step, key_prefix, puzzle_idx, error_lines_str=all_errors)

        if verifications:
            st.markdown("**Training verification**")
            show_verification_grids(verifications, examples)


def show_refinements(refinements, examples, puzzle_idx):
    """Render each refinement attempt using the generic step renderer."""
    if not refinements:
        return
    st.subheader("Refinement Attempts")
    for ref in refinements:
        attempt = ref.get("attempt", "?")
        show_step(
            ref,
            title=f"Attempt {attempt}",
            key_prefix=f"ref_{attempt}",
            examples=examples,
            puzzle_idx=puzzle_idx,
        )


# ---------------------------------------------------------------------------
# Run file discovery
# ---------------------------------------------------------------------------


def _find_run_dirs():
    """Return audit run directories from src/audit/, newest first."""
    src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    audit_dir = os.path.join(src_dir, "audit")
    if not os.path.isdir(audit_dir):
        return []
    dirs = [
        os.path.join(audit_dir, d)
        for d in os.listdir(audit_dir)
        if os.path.isdir(os.path.join(audit_dir, d))
    ]
    return sorted(dirs, key=os.path.getmtime, reverse=True)


def _load_run(run_dir):
    """Load all per-puzzle JSON files from a run directory."""
    paths = sorted(glob.glob(os.path.join(run_dir, "*.json")))
    records = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            records.append(json.load(f))
    return records


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

run_dirs = _find_run_dirs()

with st.sidebar:
    st.header("Run")
    if not run_dirs:
        st.warning("No runs found in src/audit/.")
        st.stop()

    selected_run = st.selectbox(
        "Run", options=run_dirs, format_func=os.path.basename
    )

    try:
        records = _load_run(selected_run)
    except Exception as e:
        st.error(f"Could not load {selected_run}: {e}")
        st.stop()

    n_solved = sum(1 for r in records if r.get("final_correct"))
    st.caption(f"{n_solved}/{len(records)} puzzles solved")

    st.divider()
    st.header("Puzzle")

    def _puzzle_label(r):
        status = "SOLVED" if r.get("final_correct") else "UNSOLVED"
        return f"{r['puzzle_id']} — {status}"

    puzzle_idx = st.radio(
        "Select",
        options=range(len(records)),
        format_func=lambda i: _puzzle_label(records[i]),
    )

# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------

record = records[puzzle_idx]
puzzle_id = record["puzzle_id"]
dataset = record.get("dataset", "arc-v1-training")
final_correct = record.get("final_correct", False)

status_color = "#2ECC40" if final_correct else "#FF4136"
status_text = "SOLVED" if final_correct else "UNSOLVED"
st.html(
    f'<h2 style="margin-bottom:2px">{puzzle_id} '
    f'<span style="color:{status_color};font-size:14px">● {status_text}</span></h2>'
)
st.caption(
    f"Dataset: {dataset} · {record.get('n_train_examples', '?')} training examples · "
    f"{len(record.get('refinements', []))} refinement(s)"
)

# Load the original puzzle to get input grids for visualisation.
# `load_puzzle` resolves dataset paths relative to the project's `src/`
# directory, so chdir there for the call regardless of the streamlit CWD.
try:
    from utils.arc_loader import load_puzzle
    src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    _prev_cwd = os.getcwd()
    try:
        os.chdir(src_dir)
        puzzle = load_puzzle(puzzle_id, dataset)
    finally:
        os.chdir(_prev_cwd)
    examples = puzzle["train"]
    test_examples = puzzle.get("test", [])
except Exception as e:
    st.warning(f"Could not load puzzle data: {e}")
    puzzle = {}
    examples = []
    test_examples = []

# ---------------------------------------------------------------------------
# Training example grids overview
# ---------------------------------------------------------------------------

st.subheader("Examples")
show_example_navigator(
    puzzle_idx,
    examples,
    test_examples,
    record.get("train_verifications", []),
)

# ---------------------------------------------------------------------------
# Generation steps
# ---------------------------------------------------------------------------
# Render every step under `record["steps"]` generically. The dict may carry
# one entry ("initial") or several; step key order is preserved.

steps = record.get("steps", {}) or {}
if steps:
    st.subheader("Generation")
    for step_key, step in steps.items():
        if not isinstance(step, dict):
            continue
        show_step(
            step,
            title=step_key.replace("_", " ").title(),
            key_prefix=f"step_{step_key}",
            examples=examples,
            puzzle_idx=puzzle_idx,
            expanded=True,
        )

# ---------------------------------------------------------------------------
# Legacy top-level program / verifications (shown only if present)
# ---------------------------------------------------------------------------
# Older runs stored the assembled program and its verification results at the
# top level. Newer runs embed these inside each step. Keep rendering them when
# they exist so historical audits remain viewable.

full_program = record.get("full_program", "")
top_verifications = record.get("train_verifications", []) or []

if full_program:
    st.subheader("Assembled Program")
    all_init_errors = " ".join(
        v.get("clingo_errors", "") or "" for v in top_verifications
    )
    show_program(full_program, all_init_errors)

    if top_verifications:
        st.subheader("Training Verification")
        n_pass = sum(1 for v in top_verifications if v.get("correct"))
        n_total = len(top_verifications)
        if n_pass == n_total:
            st.success(f"All {n_total} training examples passed.")
        else:
            st.error(f"{n_pass}/{n_total} passed")
        show_verification_grids(top_verifications, examples)

# ---------------------------------------------------------------------------
# Refinement attempts
# ---------------------------------------------------------------------------

show_refinements(record.get("refinements", []), examples, puzzle_idx)
