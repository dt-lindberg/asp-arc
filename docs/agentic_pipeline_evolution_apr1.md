# Agentic Pipeline Evolution — April 1 2026

This document traces the development session that produced the first working
agentic ASP synthesis pipeline, covering the commits and experimental runs
from ~20:53 to ~22:43 CEST on April 1 2026.

---

## Timeline

All times are CEST (UTC+2).

| Time | Commit | Description |
|------|--------|-------------|
| 20:53 | `852a68c` | Working agentic setup — first end-to-end run with syntax agent |
| 20:55 | `897204e` | Fix `n_cores` in `run.job` |
| 21:55 | `7856dc3` | Streamlit interface shows syntax refinement steps |
| 22:03 | `502eb8a` | Commit first 3-puzzle result set (`20260401_205614`) |
| 22:26 | `c586874` | Improve pipeline: better prompts, multi-candidate generation, smarter feedback |
| 22:30 | `bab1e6c` | Stuck detection in agent; fix aggregate patterns in `quick_syntax_fix` |
| 22:40 | `cd18714` | Add single-shot rewrite stage; 3-stage syntax pipeline |
| 22:42 | `74f6902` | Include full ASP syntax guide in rewrite system prompt and reattempt prompt |
| 22:43 | `ec84a17` | Diversify candidate prompts (cand-1 uses choice+constraints strategy) |

---

## Experimental runs and results

### Run `20260401_205614` — first agentic run

**Code state:** `852a68c` (first working agentic setup)

**Puzzles:** `8d510a79`, `39e1d7f9`, `8a004b2b` (exploratory, not the final benchmark set)

| Puzzle | Outcome | Note |
|--------|---------|------|
| `8d510a79` | FAILED | `clingo_error` on all train examples; 5 refinements, syntax agent active but couldn't fix |
| `39e1d7f9` | FAILED | same |
| `8a004b2b` | FAILED | same |

**Key observations:**
- The initial generation consistently produced programs with `#max`/`#min` aggregate
  syntax that Clingo rejects (`syntax error, unexpected #max`).
- The multi-turn tool-calling syntax agent (10 steps, `MAX_SYNTAX_ATTEMPTS=6`) ran
  to its limit without resolving the errors.
- No rewrite stage existed yet; the pipeline had no fast path out of syntax failures.
- No test evaluation was implemented.

---

### Run `20260401_221210_partial` — crashed run

**Code state:** in-progress improvements between `502eb8a` and `c586874`

**Puzzles:** `0d3d703e`, `0520fde7`, `1e0a9b12` (benchmark set introduced in `run.job`)

All three records have empty `steps`, `train_verifications`, and `refinements`.
The run crashed immediately after initialisation; the `_partial` suffix reflects this.
This was the first attempt with the new benchmark puzzle IDs after `run.job` was updated.

---

### Run `20260401_221450` — improved pipeline, first benchmark results

**Code state:** `c586874` + `bab1e6c` (pipeline improvements and stuck detection)

**Puzzles:** `0d3d703e`, `0520fde7`, `1e0a9b12`

| Puzzle | Outcome | Refinements | Initial train status |
|--------|---------|-------------|----------------------|
| `0d3d703e` | **SOLVED** | 1 | all `clingo_error` |
| `0520fde7` | FAILED | 8 (max) | all `wrong_values` |
| `1e0a9b12` | **SOLVED** | 5 | all `clingo_error` |

**Key observations:**
- 2 of 3 puzzles solved — a major step forward from the first run (0/3).
- Both solved puzzles started with `clingo_error`; the improved feedback and
  syntax agent eventually recovered them via refinement iterations.
- `0520fde7` never had a syntax error — it got past generation cleanly but
  produced logically wrong answers on all 3 train examples throughout all
  8 refinements. This is a different failure mode (underconstrained or wrong
  logic) that the pipeline had not yet learned to address.
- No `rewrite_rounds` field in `syntax_agent` records (rewrite stage not yet added).
- No test evaluation.

---

### Run `20260401_224043` — rewrite stage + syntax guide + candidate diversity

**Code state:** `cd18714` + `74f6902` + `ec84a17` (full final pipeline of the session)

**Puzzles:** `0d3d703e`, `0520fde7`, `1e0a9b12`

| Puzzle | Outcome | Refinements | Rewrite rounds | Test correct |
|--------|---------|-------------|----------------|-------------|
| `0d3d703e` | **SOLVED** | 0 | 2 | **yes** |
| `0520fde7` | FAILED | 8 (max) | — | no |
| `1e0a9b12` | **SOLVED** | 0 | 3 | **yes** |

**Key observations:**
- Both solvable puzzles were solved **on the first generation attempt** — zero
  refinement iterations needed.
- The `rewrite_rounds` field confirms the new rewrite stage was active and
  was the mechanism that fixed the syntax errors before any logical evaluation.
- The tool-calling syntax agent (`steps`) was not invoked at all for either
  solved puzzle — the single-shot rewrite handled everything.
- Test evaluation is now wired in: both solved puzzles also pass on the unseen
  test examples.
- `0520fde7` still fails identically — 8 refinements, `wrong_values` throughout,
  unchanged between the two benchmark runs. This is an open problem.

---

## What the final run introduced and why it solved puzzles first-try

Three changes landed in the last three commits and together explain the jump
from "solved after N refinements" to "solved on first generation":

### 1. Single-shot rewrite stage (`cd18714`)

Previously, syntax errors were handled exclusively by a multi-turn tool-calling
agent that called `run_clingo` and `edit_code` iteratively. This was slow and
brittle: the agent frequently failed to match `old_str` patterns correctly and
could exhaust its step budget (`MAX_SYNTAX_ATTEMPTS=10`) without fixing anything.

The rewrite stage (`rewrite_syntax_fix` in `agent.py`) is a single LLM call that
asks the model to return the **complete fixed program** in one code block. Up to
3 rounds are attempted before falling back to the tool agent. For both solved
puzzles, 2–3 rewrite rounds were sufficient and the tool agent was never needed.

The three-stage syntax pipeline (after the initial generation) is now:

```
Stage 1: quick_syntax_fix()   — regex substitutions (instant, no LLM call)
Stage 2: rewrite_syntax_fix() — up to 3 single-shot LLM rewrites
Stage 3: run_syntax_agent()   — multi-turn tool-calling agent (fallback)
```

### 2. Full ASP syntax guide in the rewrite system prompt (`74f6902`)

The rewrite call's system prompt now contains the full `docs/asp_syntax_guide.md`
(15 rules). This gives the model a complete, correct reference at the moment it is
rewriting a broken program: it sees exactly which aggregate forms Clingo accepts,
how `#count`/`#sum`/`#min`/`#max` must be written, how modulo works, and so on.
The same guide was also added to the reattempt prompt (`5_reattempt.txt`) so that
any subsequent refinement step also has this reference.

The concrete errors seen in both solved puzzles were `syntax error, unexpected #max`
on lines using `X = #max{...}` as a standalone fact. The syntax guide shows the
correct form (`max_val(X) :- X = #max{...}.`), which the model applied during
rewriting.

### 3. Two structurally different candidates (`ec84a17`)

The pipeline now generates **two candidate programs per puzzle** in parallel:

- **Candidate 0** — default direct-derivation approach (no hint)
- **Candidate 1** — explicitly prompted to use the choice+constraints strategy:
  `1 { output(R,C,V) : color(V) } 1 :- output_cell(R,C).`

Both candidates go through `quick_syntax_fix` before being evaluated on training
examples. The candidate with the best outcome is selected (preference order:
solved > no syntax error > highest accuracy). This increases the probability that
at least one candidate arrives at evaluation without syntax errors, and also gives
the pipeline two structurally distinct programs to choose from when both compile.

### Combined effect

For `0d3d703e` and `1e0a9b12`, the sequence was:

1. One of the two candidates was generated with correct logic but invalid aggregate
   syntax (`unexpected #max`).
2. `quick_syntax_fix` did not catch this pattern (it requires an LLM to rewrite
   the surrounding rule).
3. The rewrite stage made 2–3 LLM calls with the full syntax guide in context and
   produced a syntactically valid, logically equivalent program.
4. Clingo evaluated it immediately as correct on all training examples.
5. No refinement iterations were needed.

The result is that the pipeline now spends its "fix budget" on a fast, focused
syntax-only rewrite rather than burning it on multi-turn tool calls that mix
syntax debugging with logic changes.

---

## Open problem: `0520fde7`

This puzzle has produced `wrong_values` on every attempt across both benchmark
runs (16+ total attempts). Unlike the other two, generation produces a
syntactically valid program immediately — the problem is logical. The current
refinement loop does provide targeted feedback for `wrong_values` (added in
`c586874`), but the model has not managed to correct the logic within 8 attempts.

Possible directions:
- Inspect what transformation `0520fde7` requires and whether the current prompt
  guides the model toward the right reasoning strategy.
- Consider a dedicated "logic rewrite" stage analogous to the syntax rewrite —
  a single-shot call asking the model to produce a completely new program given
  the accumulated failure evidence.
- Check whether candidate diversification helps here (neither candidate is
  currently solving it on first try).
