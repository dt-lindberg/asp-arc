---
name: streamlit-inspector
description: Work with the asp-arc Streamlit inspector app (streamlit-interface/app.py). Use when asked to run, extend, or debug the visual puzzle inspector.
---

# Skill: streamlit-inspector

## Running the app

From the **project root**:

```bash
streamlit run streamlit-interface/app.py
```

The app discovers run JSON files automatically — it searches `outputs/*.json` first, then any `YYYYMMDD_HHMMSS.json` files in the project root (early runs saved before the `outputs/` dir existed).

## File layout

```
eval/
  app.py        # Single-page Streamlit app (entry point)
  __init__.py
```

`eval/app.py` imports from the project root (`arc_loader`, `config`) via `sys.path` manipulation — no install step needed.

## What the app shows

For each puzzle record in a run JSON:

| Section | Contents |
|---------|----------|
| Training examples | Colour-coded input → output grid pairs (ARC standard palette) |
| Step-by-step analysis | Collapsible expanders for each of the 4 steps (analysis, predicates, choice_rules, constraints); thinking shown when non-empty |
| Assembled program | Full ASP program with error-line highlighting (lines referenced in Clingo errors get a red background + ⚠ marker) |
| Training verification | Per-example PASS/FAIL badges; input / expected / predicted grids side by side; diff string and Clingo errors below; if all examples share the same syntax error it is shown once |
| Refinement attempts | Collapsible per-attempt view with same structure as initial verification |

## ARC colour palette

```python
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
```

## Extending the app

- **New section**: add a function that takes `record` (the puzzle dict) and call it after the existing sections in `eval/app.py`. Use `st.subheader` for the title and wrap detail in `st.expander`.
- **New page**: convert to multi-page by creating `eval/pages/<name>.py` and updating `eval/app.py` to use `st.navigation` (see `asp-gen-refinements/evaluation/app.py` for reference).
- **Grid cell size**: adjust the `cell_px` parameter in `_grid_html()` — default is 26px.

## Key data paths in a record

```python
record["puzzle_id"]                          # e.g. "007bbfb7"
record["dataset"]                            # e.g. "arc-v1-training"
record["steps"]["analysis"]["thinking"]      # LLM thinking for step 1
record["steps"]["constraints"]["extracted"]  # extracted ASP code from step 4
record["full_program"]                       # assembled program sent to Clingo
record["train_verifications"][i]["status"]   # "correct" | "clingo_error" | ...
record["train_verifications"][i]["clingo_errors"]
record["train_verifications"][i]["grid_predicted"]
record["train_verifications"][i]["grid_expected"]
record["refinements"][k]["program"]          # program from refinement attempt k+1
record["refinements"][k]["train_verifications"]
record["final_correct"]                      # True if all train examples solved
```
