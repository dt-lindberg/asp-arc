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

from utils import grid_to_input_facts

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
            st.success("Correct", icon="✅")
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
# Injected-facts program view
# ---------------------------------------------------------------------------


def show_program_with_facts(program, examples, errors_str, state_key_prefix):
    """Show program with injected facts appended for a chosen example."""
    if not program:
        st.caption("(no program)")
        return

    if examples:
        ex_labels = [f"Example {i + 1}" for i in range(len(examples))]
        chosen = st.selectbox(
            "Inject facts for",
            options=range(len(examples)),
            format_func=lambda i: ex_labels[i],
            key=f"{state_key_prefix}_ex_select",
        )
        injected = grid_to_input_facts(examples[chosen]["input"])
        display_program = program + "\n\n" + injected
    else:
        display_program = program

    show_program(display_program, errors_str)


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


def show_steps(steps, puzzle_idx):
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
                    key=f"p{puzzle_idx}_thinking_{key}",
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
                    key=f"p{puzzle_idx}_response_{key}",
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
                    key=f"p{puzzle_idx}_response_{key}",
                )


# ---------------------------------------------------------------------------
# Syntax agent display
# ---------------------------------------------------------------------------


def _count_clingo_errors(error_str):
    """Count distinct Clingo parse errors by counting '<block>:N:' occurrences."""
    if not error_str:
        return 0
    return len(re.findall(r"<block>:\d+:", error_str))


def show_syntax_agent(syntax_agent, examples, puzzle_idx):
    """
    Render the syntax-agent section between Assembled Program and Training Verification.

    * Only renders when syntax_agent is present and triggered=True.
    * Shows a status badge, error-count progression, per-round expanders,
      and a final post-fix program view with error highlighting.
    """
    if not syntax_agent or not syntax_agent.get("triggered"):
        return

    steps = syntax_agent.get("steps", [])
    initial_error = syntax_agent.get("initial_error", "")
    syntax_fixed = syntax_agent.get("syntax_fixed", False)

    n_initial = _count_clingo_errors(initial_error)
    last_error = steps[-1].get("syntax_error_after", "") if steps else ""
    n_final = _count_clingo_errors(last_error)

    # Status badge and summary line
    if syntax_fixed:
        badge_color, badge_text = "#2ECC40", "FIXED"
    elif steps:
        badge_color, badge_text = "#FFDC00", "PARTIAL FIX"
    else:
        badge_color, badge_text = "#AAAAAA", "NO PROGRESS"

    st.subheader("Syntax Agent")
    st.html(
        f'<span style="background:{badge_color};color:#111;border-radius:3px;'
        f'padding:2px 8px;font-size:12px;font-weight:600">{badge_text}</span>'
        f' <span style="font-size:12px;color:#aaa">'
        f'{n_initial} errors → {n_final} errors &nbsp;·&nbsp; {len(steps)} rounds'
        f"</span>"
    )

    # Initial error block
    if initial_error:
        with st.expander("Initial parse errors", expanded=False):
            st.code(initial_error, language=None)

    # Per-round expanders
    for step in steps:
        round_num = step.get("round", "?")
        tool_call = step.get("tool_call", {})
        tool_name = tool_call.get("name", "unknown")
        error_after = step.get("syntax_error_after", "")
        n_after = _count_clingo_errors(error_after)

        round_label = (
            f"Round {round_num} \u2014 {tool_name}"
            f" \u00b7 {n_after} error{'s' if n_after != 1 else ''} remaining"
        )

        with st.expander(round_label, expanded=False):
            thinking = step.get("thinking", "")
            if thinking and thinking.strip():
                st.markdown("**Thinking**")
                st.text_area(
                    "thinking",
                    value=thinking,
                    height=140,
                    disabled=True,
                    label_visibility="collapsed",
                    key=f"p{puzzle_idx}_syn_thinking_{round_num}",
                )

            # Tool-specific display
            params = tool_call.get("params", {})
            if tool_name == "edit_code":
                old_str = params.get("old_str", "")
                new_str = params.get("new_str", "")
                if old_str or new_str:
                    st.markdown("**Patch**")
                    col_old, col_new = st.columns(2, gap="small")
                    with col_old:
                        st.caption("old_str (removed)")
                        st.code(old_str or "(empty)", language="prolog")
                    with col_new:
                        st.caption("new_str (inserted)")
                        st.code(new_str or "(empty)", language="prolog")
            elif tool_name == "run_clingo":
                test_code = params.get("code", "")
                if test_code:
                    st.markdown("**Test snippet**")
                    st.code(test_code, language="prolog")

            # Tool result (Clingo output)
            tool_result = step.get("tool_result", "")
            if tool_result:
                st.markdown("**Result**")
                # Strip the verbose program echo that edit_code appends after "Current program:"
                display_result = re.split(r"\nCurrent program:", tool_result)[0].strip()
                st.code(display_result, language=None)

            if error_after:
                st.markdown("**Remaining errors**")
                st.code(error_after, language=None)

    # Post-fix program — show once, with error highlighting on any residual errors
    if steps:
        final_program = steps[-1].get("program_after", "")
        if final_program:
            with st.expander("Post-fix program (input to refinement loop)", expanded=False):
                show_program_with_facts(
                    final_program, examples, last_error,
                    f"p{puzzle_idx}_syntax_final",
                )


# ---------------------------------------------------------------------------
# Refinement display
# ---------------------------------------------------------------------------


def show_refinements(refinements, examples, puzzle_idx):
    """Render each refinement attempt as a collapsible expander."""
    if not refinements:
        return
    st.subheader("Refinement Attempts")
    for ref in refinements:
        attempt = ref["attempt"]
        all_ok = ref.get("all_train_correct", False)
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
                    key=f"p{puzzle_idx}_ref_thinking_{attempt}",
                )

            program = ref.get("program", "")
            all_errors = " ".join(
                v.get("clingo_errors", "") or ""
                for v in ref.get("train_verifications", [])
            )
            if program:
                st.markdown("**Program**")
                show_program_with_facts(
                    program, examples, all_errors,
                    f"p{puzzle_idx}_ref_{attempt}",
                )

            st.markdown("**Training verification**")
            show_verification_grids(ref.get("train_verifications", []), examples)


# ---------------------------------------------------------------------------
# Run file discovery
# ---------------------------------------------------------------------------


def _find_run_files():
    """Collect run JSON files from outputs/ and root (timestamp-named files)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = []

    results_dir = os.path.join(root, "results")
    if os.path.isdir(results_dir):
        files.extend(glob.glob(os.path.join(results_dir, "*.json")))

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
        sa = r.get("syntax_agent", {})
        sa_tag = ""
        if sa.get("triggered"):
            sa_tag = " [SA✓]" if sa.get("syntax_fixed") else " [SA]"
        return f"{r['puzzle_id']} — {status}{sa_tag}"

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
    test_examples = puzzle.get("test", [])
except Exception:
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
# Step-by-step reasoning
# ---------------------------------------------------------------------------

st.subheader("Step-by-Step Analysis")
show_steps(record.get("steps", {}), puzzle_idx)

# ---------------------------------------------------------------------------
# Final assembled program
# ---------------------------------------------------------------------------

st.subheader("Assembled Program")
full_program = record.get("full_program", "")
all_init_errors = " ".join(
    v.get("clingo_errors", "") or ""
    for v in record.get("train_verifications", [])
)
show_program_with_facts(full_program, examples, all_init_errors, f"p{puzzle_idx}_assembled")

# ---------------------------------------------------------------------------
# Syntax agent (runs after assembly, before verification)
# ---------------------------------------------------------------------------

show_syntax_agent(record.get("syntax_agent"), examples, puzzle_idx)

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

show_refinements(record.get("refinements", []), examples, puzzle_idx)
