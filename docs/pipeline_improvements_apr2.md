---
date: 2026-04-02
---

# Pipeline Improvements — April 2, 2026

Three open issues were addressed in one pass. All changes are in `eval.py`,
`main.py`, `config.py`, and `streamlit-interface/app.py`.

---

## 1. Test prediction storage (closes `issues/test-prediction-not-stored.md`)

**Problem:** The pipeline never ran Clingo on test inputs. Results JSON had no
predicted test outputs, so the Streamlit inspector showed blank test slides.

**Changes:**

- `eval.py`: Added `predict_on_test_examples(program, test_examples, pipeline)`.
  Analogous to `verify_on_training_examples()` but without correctness checks.
  Uses test input dimensions as bounding box. Returns per-example dicts with
  `test_idx`, `status`, `n_answer_sets`, `clingo_errors`, `grid_predicted`.

- `main.py:_make_record()`: Added `"test_predictions": []` field.

- `main.py:_run_pipeline()`: After the refinement loop and before
  `_save_results()`, calls `predict_on_test_examples()` for every puzzle and
  stores results in `records[i]["test_predictions"]`.

- `streamlit-interface/app.py`: `show_example_navigator()` now accepts an
  optional `test_predictions` argument and populates the `predicted` slot for
  test slides, showing a third grid column (Input / Expected / Predicted) when
  a prediction is available.

---

## 2. Syntax fix in refinement loop (closes `issues/syntax-fix-in-refinement-loop.md`)

**Problem:** In run `20260401_224043`, puzzle `0520fde7` spent 7 of 8 refinement
attempts stuck on a recurring `#count{…} = #count{…}` syntax error. The syntax
pipeline only ran once before refinement, so each new attempt that reintroduced
the error passed it through as-is to Clingo, got an error back as feedback text,
and often regressed to a worse structure on the next attempt.

**Changes:**

- `main.py:_run_pipeline()` refinement loop: After `extract_code_blocks()` and
  before `verify_on_training_examples()`, applies:
  1. `quick_syntax_fix()` — cheap deterministic regex patches (always applied).
  2. `_check_syntax_fn()` + `rewrite_syntax_fix()` — single-shot LLM rewrite
     (max 3 rounds), only if a syntax error is detected.
  The multi-turn tool agent is intentionally excluded (too slow per-attempt).

- Added `"syntax_fixes"` list to each refinement entry in the result JSON:
  ```json
  [{"stage": "quick_fix", "n_fixes": 1}, {"stage": "rewrite", "rounds": 2}]
  ```
  Empty list when no fix was needed.

**Expected impact:** Near-correct programs that had a trivial syntax error no
longer get discarded. The next refinement loop iteration now starts from the
syntax-clean version of the near-correct program rather than regressing.

---

## 3. Multi-candidate generation scaled to 6 (closes `issues/multi-candidate-generation-and-voting.md`)

**Problem:** Only 2 candidates were generated per puzzle. Increasing to 6
costs the same wall time (vLLM batch efficiency) and gives a higher chance that
at least one candidate arrives at evaluation with correct logic.

**Changes:**

- `config.py`: Added `N_CANDIDATES = 6`.

- `main.py`: Replaced the 2-candidate loop with a 6-candidate loop using
  distinct prompt strategies:

  | idx | strategy | suffix |
  |-----|----------|--------|
  | 0 | default | (none) |
  | 1 | choice+constraints | choice rule + constraints hint |
  | 2 | deterministic | no choice rules |
  | 3 | column-by-column | column-first helper predicates |
  | 4 | row-by-row | row-first helper predicates |
  | 5 | pattern-explicit | identify rule in comment first |

- Per-candidate syntax patching: `quick_fix` + `rewrite_syntax_fix` (max 3
  rounds) now runs on every candidate before evaluation, not just the selected
  one.

- Ranking unchanged (solved > no-syntax > highest accuracy), but now considers
  6 candidates instead of 2.

- `main.py:_make_record()`: Added `"candidates": []` field.

- Result schema per candidate:
  ```json
  {
    "idx": 2,
    "strategy": "deterministic",
    "syntax_ok": true,
    "syntax_fix_stages": ["quick_fix"],
    "n_correct": 4,
    "avg_accuracy": 0.92,
    "is_solved": false,
    "selected": false
  }
  ```

**Expected impact:** Higher base rate of at least one candidate having correct
logic, reducing the work left for the refinement loop. The `candidates` schema
makes it easy to analyze which strategies work best across puzzles.
