---
name: Run syntax fix pipeline after each refinement attempt
status: closed
---

# Run syntax fix pipeline after each refinement attempt

## Summary

The 3-stage syntax pipeline (quick_fix → rewrite → tool_agent) currently runs
only once, between the initial generation and the refinement loop. Refinement
attempts that introduce syntax errors receive no automatic fix — the error is
passed back as feedback text and the model tries again from scratch.

## Observed problem

In `20260401_224043`, puzzle `0520fde7` spent refinements 1–5 and 7–8 stuck on
`clingo_error` caused by a recurring `#count{…} = #count{…}` inline aggregate
comparison. Each attempt received the Clingo error as feedback, rewrote the
whole program, and reintroduced the same syntax bug. At refinement 6 the model
accidentally avoided it and reached acc=0.89 — but the following attempt
regressed. A syntax fix applied after refinement 6 would have let the model stay
on the near-correct program and refine logic only.

## What needs to change

### `main.py` — wrap each refinement output in the syntax pipeline

Inside the refinement loop, after the new program is extracted and before
`verify_on_training_examples` is called, apply the same 3-stage fix:

```python
for i, (thinking, response) in zip(active, gen_results):
    new_program = extract_code_blocks(response)

    # --- NEW: patch syntax before evaluating ---
    quick_fixed, n_q = quick_syntax_fix(new_program)
    if n_q > 0:
        new_program = quick_fixed

    syntax_err = _check_syntax_fn(new_program, pipeline)
    if syntax_err:
        rewritten, _, rewrite_err = rewrite_syntax_fix(
            new_program, syntax_err, engine, pipeline, max_rewrites=3
        )
        if rewrite_err is None:
            new_program = rewritten
        # If rewrite also fails, fall through — verify will record clingo_error
        # and the error text becomes feedback for the next attempt.
    # --- END NEW ---

    new_train_results = verify_on_training_examples(...)
```

The multi-turn tool agent is intentionally excluded from the per-refinement
path — it is too slow to run on every attempt. The quick_fix + rewrite pair
adds at most 3 extra LLM calls per attempt only when a syntax error is present.

### Feedback message when rewrite fails

If the rewrite cannot fix the error, the clingo error should still be included
in the feedback string so the model knows what broke. This is already the case
via `build_train_feedback`, which reads `clingo_errors` from the verification
result — no change needed there.

### Result schema

Add a `syntax_fixes` list to each `refinements` entry to record when a fix was
applied:

```json
{
  "attempt": 3,
  "syntax_fixes": [{"stage": "quick_fix", "n_fixes": 1}, {"stage": "rewrite", "rounds": 2}],
  "program": "...",
  ...
}
```

This field can be omitted (or `[]`) when no fix was needed.

## Expected impact

- Prevents the oscillation pattern where a refinement that nearly works gets
  discarded because it has a trivial syntax error, and the next attempt
  regresses to an earlier (worse) structure.
- Gives the rewrite stage a chance to apply the syntax guide rules on every
  generated program, not just the first one.
- No change to the refinement loop's iteration count or stopping condition.
