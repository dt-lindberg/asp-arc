---
name: Multi-candidate generation with syntax patching and voting
status: closed
---

# Multi-candidate generation with syntax patching and voting

## Summary

Scale the current 2-candidate generation approach to 6–8 candidates, apply the
full syntax fix pipeline to each, evaluate all candidates on training examples,
and select the best one via a structured voting/ranking scheme. This adapts the
multi-expert + voting pattern from `docs/arc-agi-harness.md` to the ASP setting
where a single GPU runs one model in large batches.

## Background

The current pipeline generates 2 candidates per puzzle with different prompt
strategies (direct derivation vs. choice+constraints), applies `quick_syntax_fix`
before evaluation, and picks the better one. This already helps: in
`20260401_224043`, having two structurally different candidates increased the
probability that at least one arrived at evaluation with correct logic.

The natural extension is to treat the initial generation as a **sampling** step:
generate N programs from the same model at high temperature, patch their syntax,
evaluate them all, and select the most promising one. When multiple candidates
independently produce the same train output, that agreement is strong evidence
of correctness — the same self-consistency principle used in `solve_parallel_coding.py`
from the harness (`docs/arc-agi-harness.md` §3.9).

## Why this fits the vLLM setup

The model is served via vLLM, which processes requests in large batches
efficiently. Generating 8 candidates for one puzzle costs roughly the same wall
time as generating 1 candidate for 8 puzzles — the batch simply fills up one
forward pass. There is no penalty for increasing N as long as the total batch
size stays within the GPU's memory bandwidth budget. Temperature should be kept
high (≥ 0.7) to ensure the candidates are structurally diverse.

## Proposed design

### 1. Candidate generation

Generate `N_CANDIDATES` (suggested: 6–8) programs per puzzle in a single batched
call. Prompt diversity strategies to distribute across candidates:

| Candidate | Prompt suffix / strategy |
|-----------|--------------------------|
| 0 | default (no hint) |
| 1 | choice + constraints hint |
| 2 | no hint, different seed |
| 3 | ask for a purely deterministic (no choice rules) program |
| 4 | ask for a program that computes output column-by-column |
| 5–7 | no hint, varied temperature or seed |

All candidates share the same puzzle formatting; only the strategy suffix or
seed differs.

### 2. Syntax patching per candidate

Run the full 3-stage syntax pipeline on each candidate independently:

```
quick_syntax_fix → rewrite_syntax_fix (max 3 rounds) → drop if still broken
```

Candidates that cannot be fixed after the rewrite stage are still kept but
flagged as `syntax_broken=True` and ranked last. They should not block
candidates that compiled cleanly.

### 3. Evaluation

Run `verify_on_training_examples` on all syntax-clean candidates. Collect:
- `n_correct`: number of training examples passed
- `avg_accuracy`: mean per-cell accuracy across training examples
- `is_solved`: all training examples correct
- `predicted_outputs`: the actual grid predictions for each training example

### 4. Selection / voting

Rank candidates using this priority order (mirrors the harness approach):

1. **Solved candidates** — if any candidate passes all training examples, pick
   the one with the highest train accuracy (or, if multiple are solved, the one
   whose predicted test output appears most often among all solved candidates —
   majority vote on test output grids).
2. **No-syntax candidates** — among unsolved candidates, prefer those without
   syntax errors; rank by `avg_accuracy` descending.
3. **Syntax-broken candidates** — rank last, by `n_correct` descending.

If multiple candidates are solved and their test outputs differ, use output
grid majority vote to pick the representative for attempt 1, and the next
most-voted for attempt 2 (matching ARC-AGI's 2-attempt submission format).

### 5. Pass the best candidate into the refinement loop

The selected candidate becomes `programs[i]` and enters the existing refinement
loop unchanged. The only difference is that the starting point for refinement
is now the best of N independent samples rather than the better of 2.

## Result schema additions

```json
{
  "candidates": [
    {
      "idx": 0,
      "strategy": "default",
      "syntax_ok": true,
      "syntax_fix_stages": ["quick_fix"],
      "n_correct": 3,
      "avg_accuracy": 1.0,
      "selected": true
    },
    ...
  ]
}
```

## Configuration

Add to `config.py`:

```python
N_CANDIDATES = 8          # number of initial candidates to generate
CANDIDATE_TEMPERATURE = 0.8  # higher than default for diversity
```

## Expected impact

- Higher probability that at least one of N candidates has the right logic from
  the start, reaching the refinement loop with a better initial program.
- Majority voting on test outputs provides a soft confidence signal for free —
  if 5 of 8 candidates agree on a test output grid, submit that with high
  confidence.
- Puzzles like `0520fde7` where the model has the right intuition but keeps
  introducing syntax bugs may see improvement: with 8 candidates at least one
  is more likely to arrive at a syntax-clean version of the near-correct program.
- No change to the refinement loop or any downstream logic.

## Relation to harness

The harness (`docs/arc-agi-harness.md`) runs up to 8 independent experts each
doing their own 10-iteration refinement loop and votes on final test outputs.
This issue proposes a lighter version: vote at generation time only, then run
a single shared refinement loop on the winner. A future extension could run
independent refinement loops per candidate and vote at the end — but that is
a larger architectural change and should be a separate issue.
