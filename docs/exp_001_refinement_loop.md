# Experiment 001 — Agentic Refinement Loop

## Hypothesis

Adding a back-and-forth refinement loop (up to 2 rounds) that feeds Clingo
error messages and grid diffs back to GPT-OSS-120B will significantly increase
the number of solved puzzles vs. the single-shot baseline.

**Expected improvement:** The baseline is 3/9 puzzles solved (with syntax guide
+ 4 candidates). I expect at least 5/9 puzzles solved with the refinement loop
on the same puzzle set.

## Mechanism

For each candidate that fails initial validation:
1. Categorize the first failure as: syntax_error / unsat / multi_answer_set / wrong_cells
2. Build a feedback message with line-numbered code + error details + grid diff
3. Send as a follow-up conversation turn
4. Re-validate the fixed program
5. Repeat up to MAX_REFINEMENT_ATTEMPTS (default 2)

Early-stopping: once any candidate for a puzzle passes, stop refining other
candidates for that puzzle.

## Code Changes

- `src/config/config.py`: Added `MAX_REFINEMENT_ATTEMPTS = 2`
- `src/utils/refinement.py`: New module with failure categorization, grid diff,
  feedback message builder, conversation history builder
- `src/prompts/refinement_feedback.txt`: Feedback prompt template
- `src/batch_pipeline.py`: Refinement loop integrated into the batch pipeline
- `src/llm/vllm_engine.py`: Added `VLLM_REQUEST_TIMEOUT` (7200s) to prevent timeout on long completions
- `src/config/config_llm.py`: Added `VLLM_REQUEST_TIMEOUT` (env-overridable)
- `jobs/run_batch_nvarc.job`: Added `MAX_REFINEMENT_ATTEMPTS` env var passthrough

## Smoke Test

```
N_PUZZLES=2 N_CANDIDATES=2 MAX_REFINEMENT_ATTEMPTS=1 sbatch --export=ALL jobs/run_batch_nvarc.job
```

Job ID: 22481376 (fixed timeout; --export=ALL required for env var passthrough)
Result: **4/4 puzzles solved**

Breakdown:
- 2 puzzles solved by initial generation (candidate diversity)
- 2 puzzles solved by refinement round 1 (both were syntax errors)
- 2/2 refinement rounds succeeded (100% refinement success rate)
- All failures were syntax errors; no semantic failures observed in this small sample

## Full Run

```
N_PUZZLES=10 N_CANDIDATES=4 MAX_REFINEMENT_ATTEMPTS=2 sbatch jobs/run_batch_nvarc.job
```

Job ID: 22481916
Result: TBD

## Analysis

TBD — fill after results.

## Conclusion

TBD
