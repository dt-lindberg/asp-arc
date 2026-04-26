# Experiment status — 2026-04-26

## What's running

9 SLURM jobs on `gpu_h100`, all started ~17:48–17:49, expected finish ~19:00–19:15.
Each runs the same 20 ARC-v1 training puzzles (locked via `--puzzle_ids`),
1 initial + 3 refinements per puzzle, only the LLM sampling seed differs.

| variant | seed | slurm | prompt file |
|---|---|---|---|
| v0 (control, 221 lines) | 132 | 22261898 | `prompts/initial_prompt.txt` |
| v0 | 7    | 22261899 | same |
| v0 | 2026 | 22261900 | same |
| v1 (minimal guide, 137 lines) | 132 | 22261901 | `prompts/initial_prompt_v1_minimal.txt` |
| v1 | 7    | 22261902 | same |
| v1 | 2026 | 22261903 | same |
| v2 (examples-only, 152 lines) | 132 | 22261904 | `prompts/initial_prompt_v2_examples.txt` |
| v2 | 7    | 22261905 | same |
| v2 | 2026 | 22261909 | same |

## What's done

### 1. Snippet-validated the current syntax guide

See `experiments/snippet_validation_report.md`. Of 33 testable snippets in
`initial_prompt.txt`, **3 "Correct:" examples are themselves syntactically
incomplete or unsafe** when read as standalone Clingo:

- Line 53: `1 { pattern : eq(X, 0) } 1` — fragment, missing `.`.
- Line 57: `(C + L) \ 2 = 0.` — fact form, `C` and `L` unbound.
- Line 79: `:- not output(R1,C1,V1).` — constraint, all vars unbound.

If the LLM lifts these verbatim, they generate exactly the unsafe-variable
errors that dominate the failure pile. Suggested fixes are in the report.

The R3 fix to **Section 6** (the "C and Color are local to aggregate"
rewrite) is verified correct — the original "global" claim was wrong.

### 2. Authored two ablation prompts

- **V1 minimal** (`initial_prompt_v1_minimal.txt`, 137 lines): drops the
  numbered Wrong/Unsafe/Safe pairs in favor of a short positive cheat-sheet.
  Tests deepseek-v4's hypothesis that "showing wrong examples leaks bad
  syntax." Bug-fix versions of the 3 problematic Correct examples are
  not present (the affected sections were dropped wholesale).
- **V2 examples-only** (`initial_prompt_v2_examples.txt`, 152 lines): drops
  the entire `<clingo_syntax_reference>` block, replaced by 3 worked
  examples (copy / recolor / mirror). Both new examples were verified to
  produce exactly 1 correct answer set under stub input grids.

### 3. Built `compare_runs.py`

Reads `src/audit/<run_id>/*.json` and tabulates: solved puzzles, clingo
errors split by category (unsafe_var / syntax / undefined_atom / other),
unsatisfiable count, wrong_values count, empty programs, mean program
length. Aggregates by variant across seeds with mean ± stdev.

### 4. Retroactive read of deepseek's R1/R2/R3 with this metric

| Run | Prompt | Solved | Clingo err | Unsafe | Syntax | Unsat | Wrong | Empty |
|-----|--------|--------|------------|--------|--------|-------|-------|-------|
| 20260426_111254 (R1) | baseline | 0/20 | 215 | 65  | 139 | 36 | 17 | 3 |
| 20260426_123318 (R2) | +comma/dot/not fixes | 0/20 | 218 | 70 | 135 | 35 | 15 | 4 |
| 20260426_152938 (R3) | +safety expansion | 0/20 | 216 | 66 | 135 | 30 | 22 | 5 |

**Reading**: at the (puzzle × attempt × train-example) failure-unit level,
the three runs are *statistically indistinguishable* — clingo errors land
at 215/218/216 (variance < 1.5%). Deepseek-v4's reported R3 = 889 unsafe
errors was counting per-clingo-log-line, where each unsafe rule emits one
note per unbound variable; the dominant signal in those numbers is "the
model wrote rules with more unbound variables", not "more rules failed".

**Also notable**: parse-level **syntax** errors (~135) outweigh
**unsafe-variable** errors (~65) by ~2×, but deepseek-v4 spent all three
rounds on safety. The bigger pile is bare-syntax mistakes (`#mod`, `//`,
`(grouped)` body, unexpected operators, etc.).

## What's next (after jobs finish)

1. Run `python experiments/compare_runs.py` (no args) — picks up the 9 new
   audit dirs by their `_v*_s*` tags and prints per-variant aggregates.
2. Statistical test: with 3 seeds per variant, a meaningful delta between
   variants should be visible at >1 sigma (n=3 is small but anything
   ≥2-sigma separation between V0 and V1/V2 is real).
3. Decide:
   - **V1 ≈ V0** ⇒ guide content is mostly inert; move to refinement-prompt
     surgery and/or port `quick_syntax_fix` regex repair.
   - **V1 < V0** (better) ⇒ deepseek-v4's "less is more" hypothesis is
     correct. Iterate: even-shorter V1, more positive worked examples.
   - **V1 > V0** (worse) ⇒ guide content does help; the bugs found in
     snippet validation may be the issue. Patch them and re-run.
   - **V2 < V0** ⇒ strongest signal that examples beat prose. Add more.
   - **V2 > V0** ⇒ at least some grammar reference helps; keep V0
     scaffolding and improve.
