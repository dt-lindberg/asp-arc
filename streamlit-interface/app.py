"""
Streamlit inspector for asp-arc pipeline runs.

* Sidebar: run file selector + puzzle list
* Main area: puzzle grids, step-by-step reasoning, generated program,
  per-example verification results, and refinement history

Usage (from project root):
    streamlit run eval/app.py
"""

import glob
import json
import os
import re
import sys

import streamlit as st

# Project root on path so arc_loader, config etc. are importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


def show_example_grids(examples):
    """Show input → output grid pairs for a list of training examples."""
    pairs = []
    for i, ex in enumerate(examples):
        inp_html = _grid_html(ex["input"])
        out_html = _grid_html(ex["output"])
        pairs.append(
            f'<div style="display:inline-block;margin:6px 20px 6px 0">'
            f'<div style="font-size:11px;color:#999;margin-bottom:4px;text-align:center">'
            f'Example {i + 1}</div>'
            f'<div style="display:flex;gap:10px;align-items:center">'
            f'{inp_html}'
            f'<span style="font-size:18px;color:#888">→</span>'
            f'{out_html}'
            f'</div>'
            f'</div>'
        )
    st.html(
        '<div style="display:flex;flex-wrap:wrap;gap:4px">' + "".join(pairs) + "</div>"
    )


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
# Step display
# ---------------------------------------------------------------------------

STEP_LABELS = {
    "analysis":     "Step 1 — Transformation Analysis",
    "predicates":   "Step 2 — Predicate List",
    "choice_rules": "Step 3 — Choice Rules",
    "constraints":  "Step 4 — Constraints",
}

# Steps that produce ASP code (extracted field is code, not NL prose)
ASP_STEPS = {"choice_rules", "constraints"}


def show_steps(steps):
    """Render the 4 generation steps as collapsible expanders."""
    for key, label in STEP_LABELS.items():
        if key not in steps:
            continue
        step = steps[key]
        with st.expander(label, expanded=False):
            thinking = step.get("thinking", "")
            if thinking and thinking.strip():
                st.markdown("**Thinking**")
                st.text_area(
                    "thinking",
                    value=thinking,
                    height=180,
                    disabled=True,
                    label_visibility="collapsed",
                    key=f"thinking_{key}",
                )

            st.markdown("**Response**")
            response = step.get("response", "")
            if key in ASP_STEPS:
                extracted = step.get("extracted", "")
                # Show full response as prose, then extracted code separately
                st.text_area(
                    "response",
                    value=response,
                    height=160,
                    disabled=True,
                    label_visibility="collapsed",
                    key=f"response_{key}",
                )
                if extracted and extracted != response:
                    st.markdown("**Extracted program**")
                    show_program(extracted)
            else:
                st.text_area(
                    "response",
                    value=response,
                    height=200,
                    disabled=True,
                    label_visibility="collapsed",
                    key=f"response_{key}",
                )


# ---------------------------------------------------------------------------
# Refinement display
# ---------------------------------------------------------------------------


def show_refinements(refinements, examples):
    """Render each refinement attempt as a collapsible expander."""
    if not refinements:
        return
    st.subheader("Refinement Attempts")
    for ref in refinements:
        attempt = ref["attempt"]
        all_ok = ref.get("all_train_correct", False)
        status_badge = "SOLVED" if all_ok else "UNSOLVED"
        badge_color = "#2ECC40" if all_ok else "#FF4136"

        label = (
            f"Attempt {attempt} — "
            f"<span style='color:{badge_color}'>{status_badge}</span>"
        )
        with st.expander(f"Attempt {attempt} — {'SOLVED' if all_ok else 'UNSOLVED'}", expanded=False):
            thinking = ref.get("thinking", "")
            if thinking and thinking.strip():
                st.markdown("**Thinking**")
                st.text_area(
                    "thinking",
                    value=thinking,
                    height=160,
                    disabled=True,
                    label_visibility="collapsed",
                    key=f"ref_thinking_{attempt}",
                )

            program = ref.get("program", "")
            # Collect all errors across examples to highlight lines
            all_errors = " ".join(
                v.get("clingo_errors", "") or ""
                for v in ref.get("train_verifications", [])
            )
            if program:
                st.markdown("**Program**")
                show_program(program, all_errors)

            st.markdown("**Training verification**")
            show_verification_grids(ref.get("train_verifications", []), examples)


# ---------------------------------------------------------------------------
# Run file discovery
# ---------------------------------------------------------------------------


def _find_run_files():
    """Collect run JSON files from outputs/ and root (timestamp-named files)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = []

    outputs_dir = os.path.join(root, "outputs")
    if os.path.isdir(outputs_dir):
        files.extend(glob.glob(os.path.join(outputs_dir, "*.json")))

    # Root-level timestamp files from early runs before outputs/ existed
    for f in glob.glob(os.path.join(root, "*.json")):
        if re.match(r"\d{8}_\d{6}\.json$", os.path.basename(f)):
            files.append(f)

    return sorted(set(files), key=os.path.getmtime, reverse=True)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

run_files = _find_run_files()

with st.sidebar:
    st.header("Run")
    if not run_files:
        st.warning("No run JSON files found in outputs/ or project root.")
        st.stop()

    selected_file = st.selectbox(
        "Run file", options=run_files, format_func=os.path.basename
    )

    try:
        with open(selected_file) as f:
            records = json.load(f)
    except Exception as e:
        st.error(f"Could not load {selected_file}: {e}")
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

# Load the original puzzle to get input grids for visualisation
try:
    from arc_loader import load_puzzle
    puzzle = load_puzzle(puzzle_id, dataset)
    examples = puzzle["train"]
except Exception:
    examples = []

# ---------------------------------------------------------------------------
# Training example grids overview
# ---------------------------------------------------------------------------

st.subheader("Training Examples")
if examples:
    show_example_grids(examples)
else:
    st.caption("Could not load puzzle data.")

# ---------------------------------------------------------------------------
# Step-by-step reasoning
# ---------------------------------------------------------------------------

st.subheader("Step-by-Step Analysis")
show_steps(record.get("steps", {}))

# ---------------------------------------------------------------------------
# Final assembled program
# ---------------------------------------------------------------------------

st.subheader("Assembled Program")
full_program = record.get("full_program", "")
if full_program:
    # Collect syntax errors across all initial verifications to highlight lines
    all_init_errors = " ".join(
        v.get("clingo_errors", "") or ""
        for v in record.get("train_verifications", [])
    )
    show_program(full_program, all_init_errors)
else:
    st.caption("(no program)")

# ---------------------------------------------------------------------------
# Training verification results
# ---------------------------------------------------------------------------

st.subheader("Training Verification")
train_verifications = record.get("train_verifications", [])

if train_verifications:
    # Aggregate status summary
    n_pass = sum(1 for v in train_verifications if v.get("correct"))
    n_total = len(train_verifications)
    unique_statuses = list({v["status"] for v in train_verifications if not v["correct"]})

    if n_pass == n_total:
        st.success(f"All {n_total} training examples passed.")
    else:
        fails = n_total - n_pass
        st.error(
            f"{n_pass}/{n_total} passed · {fails} failed"
            + (f" ({', '.join(unique_statuses)})" if unique_statuses else "")
        )

    # If all examples share the same syntax error, show it once rather than per-example
    clingo_errors = [v.get("clingo_errors") or "" for v in train_verifications]
    if len(set(clingo_errors)) == 1 and clingo_errors[0] and not any(
        v.get("correct") for v in train_verifications
    ):
        st.markdown("**Clingo errors (same for all examples)**")
        st.code(clingo_errors[0], language=None)
    else:
        show_verification_grids(train_verifications, examples)
else:
    st.caption("(no verification data)")

# ---------------------------------------------------------------------------
# Refinement attempts
# ---------------------------------------------------------------------------

show_refinements(record.get("refinements", []), examples)
