---
name: Test prediction not stored in results
status: open
---

# Test prediction not stored in results

## Summary

The pipeline never runs Clingo on the test input examples and therefore `results/*.json` contains no predicted test output. The Streamlit inspector can only show test input grids with no corresponding prediction.

## Root cause

`main.py:_make_record()` does not include a `test_predictions` field, and `_run_pipeline()` never invokes Clingo on `puzzle["test"]`. The only evaluation that runs is `eval.verify_on_training_examples()`, which is called three times (after generation, after syntax agent, after each refinement attempt) — all against training examples only.

After the refinement loop there is a clear point where `programs[i]` holds the final program for each puzzle, but it is immediately saved to JSON without a test pass:

```python
# main.py:334 — save happens here, no test evaluation above it
_save_results(records, run_id)
```

## What needs to change

### 1. `eval.py` — add `predict_on_test_examples()`

A new function analogous to `verify_on_training_examples()` but without a correctness check (test outputs are hidden in ARC-AGI). It should:

- Skip the bare-program syntax check (already done before this point).
- For each test input, inject `input(R,C,V)` facts, run Clingo, extract `output(R,C,V)` atoms.
- Use the test input grid dimensions as the bounding box for `answer_set_to_grid()` — this is a heuristic since test output dimensions are unknown, but it matches common ARC puzzle structure (output same size as input). Any atoms outside those bounds are silently dropped, which is acceptable for display purposes.
- Return a list of dicts with the same shape as training verification results, minus `grid_expected`, `correct`, `diff`, and `accuracy`.

Example return structure per test example:
```python
{
    "test_idx": 0,
    "status": "predicted",        # or "clingo_error" / "unsatisfiable" / "underconstrained"
    "n_answer_sets": 1,
    "clingo_errors": "",
    "grid_predicted": [[0, 1, ...], ...],
}
```

### 2. `main.py` — call it after the refinement loop

After the refinement loop and before `_save_results()`, for each puzzle:

```python
# Run final program on test inputs and store predicted grids
for i in range(n):
    test_preds = predict_on_test_examples(programs[i], puzzles[i]["test"], pipeline)
    records[i]["test_predictions"] = test_preds
```

### 3. `main.py:_make_record()` — add field to record template

```python
"test_predictions": [],
```

### 4. Streamlit app — display test predictions

The `show_example_navigator()` function already receives `test_examples` and renders test slides. The slide dict already has a `predicted` key but it is always `None` for test examples. Once `test_predictions` is stored, the navigator can populate `predicted` from it:

```python
for i, ex in enumerate(test_examples):
    tp = test_pred_by_idx.get(i, {})
    slides.append({
        ...
        "predicted": tp.get("grid_predicted"),
        "correct": None,   # never known for test
    })
```

## Notes / caveats

- **Output grid size**: `answer_set_to_grid(atoms, n_rows, n_cols)` requires explicit bounds. Using the test input dimensions is a reasonable default but will silently crop any atoms outside that range. Could alternatively derive bounds from the max row/col indices in the atoms themselves.
- **Multiple answer sets**: if the program is underconstrained it produces >1 answer set. The training verifier marks this as an error. For test prediction the same logic applies — show the first answer set if exactly one exists, otherwise report the status.
- **Timing**: test prediction adds one Clingo call per test example per puzzle. This is cheap (milliseconds) and sequential, so it does not affect the batched LLM generation stage.
- **Partial runs**: the `_partial` save on pipeline crash (main.py:131) should also include test predictions if they have been computed by that point. This is handled automatically if the loop above runs before the save.
