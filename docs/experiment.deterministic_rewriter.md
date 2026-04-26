# asp-arc-rewrites: deterministic ASP rewriter

## Context

The asp-arc pipeline (in `~/Projects/asp-arc`) asks Nemotron-Cascade-2 to produce Clingo/ASP programs that solve ARC-AGI grid-transformation puzzles. The dominant bottleneck is **syntactic, not semantic**: across 88 puzzle audits in `~/Projects/asp-arc/src/audit/2026042*/`, **0/88 puzzles solved** end-to-end and **82% of refinement-attempt transitions stay in `clingo_error → clingo_error`**. Most syntax errors are mechanical and repeat across puzzles (`#mod` instead of `\`, aggregate on the left of `=`, markdown fences leaking into the program, stray `:-` continuations, etc.).

This plan describes work that takes place in a **separate worktree, `~/Projects/asp-arc-rewrites/`**, branching off `feature/baseline`. All paths in this plan are relative to that worktree unless prefixed with `~/Projects/asp-arc/`.

We want a **deterministic, logic-preserving rewriter** that takes an ASP program with these mechanical errors and returns a corrected one. It must be testable offline against the historic audit corpus (read from `~/Projects/asp-arc/src/audit/`), with no LLM and no GPU. Built as an isolated object that can later be slotted into the pipeline as a single line — when given a clean program, it does nothing.

## Goal

Fix the most common, mechanical syntax errors in LLM-emitted ASP programs by applying a small set of deterministic, predefined rewrite rules. Measure the fix rate against the historic audit data.

## Design (minimal)

Three pieces, nothing more.

### 1. The rewriter object
```python
class ASPRewriter:
    def rewrite(program: str) -> str
```
- Stateless, pure text in / text out.
- Holds an ordered list of `Rule` instances.
- Loops: apply each rule until none changes the text (fixed point), then return.
- No I/O, no Clingo calls inside.

### 2. A rule
```python
class Rule:
    name: str
    def apply(program: str) -> str
```
- One small function per rule.
- If the pattern doesn't match, return the input unchanged.
- Logic preservation is enforced by *writing rules that don't change logic* — verified by unit tests, not by runtime machinery.

### 3. The test harness
- `tests/test_rewriter.py` — small unit tests per rule with hand-picked `(before, after)` fixtures, plus an "unchanged" fixture.
- `scripts/evaluate_rewriter.py`:
  1. Walk every audit JSON under `~/Projects/asp-arc/src/audit/`, pull every `program` whose attempt status is `clingo_error`.
  2. Run each through `ASPRewriter().rewrite(...)`.
  3. Run Clingo on both the original and the rewritten program (with stub `input/3` + `color/1` injected) and classify outcome: `still_broken`, `fixed`, `different_error`.
  4. Print a table: per-rule fire count, total fix count, residual error breakdown.
  5. Append the cumulative result `(rule_count, total_fixed, rule_short_description)` to `results/rewriter_progress.csv` so the final plot can be regenerated at any time.

## Starting rules (pick 2–3 to begin with)

Start with the easiest, highest-mass ones so the harness produces a real number on day one:

| Rule | What it does |
|---|---|
| `mod_to_backslash` | `#mod` → `\` (36 lexer errors in audits) |
| `strip_fences` | drop `` ` ``, `<asp>`, `</asp>`, `<clingo>`, leading prose lines (57 backtick + 39 empty-program errors) |
| `agg_on_left` | `#count{…} = X` → `X = #count{…}` (and `#min`/`#max`/`#sum`) (~626 unexpected-`=` lines) |

Candidates for the next batch (only if measured to help):
- `agg_parens_to_braces` — `#count(...)` → `#count{...}`
- `strip_stray_colon` — indented continuation lines starting with `:-`
- `drop_trailing_comma` — `, }` / `, ;` inside aggregate bodies
- `swi_aggregate` — SWI-Prolog `aggregate/3`, `aggregate_all/3` → Clingo equivalents
- `lowercase_const` — `#const NAME = …` → lowercase

## Files (all paths relative to `~/Projects/asp-arc-rewrites/`)

```
src/utils/rewriter.py              # ASPRewriter + rules in one file to start
tests/test_rewriter.py             # unit tests per rule (small fixtures)
scripts/evaluate_rewriter.py       # runs against historic audits, prints summary
scripts/plot_rewriter_progress.py  # generates the final cumulative-fixes plot
docs/rewriter.md                   # design doc for the repo
evaluate_rewriter.job              # SLURM job to run the evaluation
results/rewriter_progress.csv      # per-rule cumulative metrics (generated)
results/rewriter_progress.png      # final plot (generated)
```

Critical files for later integration (not modified in this plan):
- `src/agent/agent.py` — eventual single-line plug-in: `program = ASPRewriter().rewrite(program)` after `extract_code_blocks`.

## Build & evaluation procedure (driven by an agent)

Spawn an agent inside `~/Projects/asp-arc-rewrites/` to construct the rewriter and grow it one rule at a time, measuring after each addition.

1. **Scaffold (rule count = 0).**
   - Create `src/utils/rewriter.py` with `ASPRewriter` and an empty rule list, `tests/test_rewriter.py`, and `scripts/evaluate_rewriter.py`.
   - Run the harness once to produce the baseline row: `0 rules, 0 fixed, "baseline"`.

2. **Add one rule at a time.**
   For each rule (starting with `mod_to_backslash`, `strip_fences`, `agg_on_left`, then the next-batch candidates as long as they help):
   1. Implement the rule as a single function in `src/utils/rewriter.py`.
   2. Add `(before, after)` and `(unchanged)` unit tests in `tests/test_rewriter.py`. Run `pytest`.
   3. Append the rule to `ASPRewriter`'s rule list.
   4. Submit `sbatch evaluate_rewriter.job` (see below). It writes a row to `results/rewriter_progress.csv`:
      `rule_index, rule_name, short_description, total_fixed, delta_fixed, regressions`.
   5. If `delta_fixed <= 0` or `regressions > 0`, fix or drop the rule before continuing.
   6. Commit. Move to the next rule.

3. **Stop condition.** Halt when adding a rule fails to fix at least one previously broken program, or when the candidate list is exhausted.

4. **Plot.**
   - `scripts/plot_rewriter_progress.py` reads `results/rewriter_progress.csv` and produces `results/rewriter_progress.png`:
     - x-axis: cumulative number of rules used (with each rule labelled by its 2-word description, e.g. `mod→\`, `strip fences`, `agg left→right`).
     - y-axis: cumulative number of programs fixed (out of `total_clingo_error_attempts` in the corpus).
     - Stepwise line plot; annotate each step with `+Δ` (programs fixed by that specific rule).

## Verification

- `pytest tests/test_rewriter.py` passes after every rule addition.
- `results/rewriter_progress.csv` is monotonically non-decreasing in `total_fixed` and has zero regressions across rows.
- `results/rewriter_progress.png` exists and shows the cumulative fix curve with rule labels.
- Headline metric: `final total_fixed / total_clingo_error_attempts` (denominator computed from the audit corpus). Reported in the plot title.

### Audit corpus to evaluate against

`scripts/evaluate_rewriter.py` must walk **every** subdirectory of `~/Projects/asp-arc/src/audit/` (not just one timestamp). At time of writing this is 17 directories ranging from `20260422_082132` to `20260426_111254`, totalling well over 88 puzzles — many puzzles appear in multiple runs, which gives more diverse `(program, error)` pairs from the same puzzle and is good signal. Deduplicate identical `program` strings across runs before tallying so the metric isn't inflated by reruns. Read with `glob.glob(os.path.expanduser("~/Projects/asp-arc/src/audit/*/*.json"))`.

### SLURM job for offline evaluation

The evaluation runs Clingo over the corpus, which is CPU-intensive — it must not run on the login node. Create `~/Projects/asp-arc-rewrites/evaluate_rewriter.job`, mirroring `~/Projects/asp-arc/install_env_qwen36.job` but on the `rome` (CPU) partition and reusing the existing `.venv` (already has Clingo installed):

```
#!/bin/bash
#SBATCH --job-name=RewriterEval
#SBATCH --partition=rome
#SBATCH --time=00:30:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --output=slurm_output_%A.out

module purge
module load 2025
module load Python/3.13.5-GCCcore-14.3.0

cd $HOME/Projects/asp-arc-rewrites
source .venv/bin/activate

python3 scripts/evaluate_rewriter.py
python3 scripts/plot_rewriter_progress.py

echo "[SLURM] Exiting"
```

Submit with `sbatch evaluate_rewriter.job` from inside `~/Projects/asp-arc-rewrites/`. The job appends one row per current rule set to `results/rewriter_progress.csv` and regenerates `results/rewriter_progress.png`. Re-run after every rule addition.

Note: the `.venv` lives in `~/Projects/asp-arc/.venv` and is shared via the worktree's relationship to the parent repo. If `~/Projects/asp-arc-rewrites/.venv` doesn't exist, symlink it (`ln -s ~/Projects/asp-arc/.venv ~/Projects/asp-arc-rewrites/.venv`) rather than re-installing.

### Live pipeline check (later)

Only after the offline numbers look good: from `~/Projects/asp-arc/`, run `sbatch run.job` on the same 20-puzzle cohort used for `src/audit/20260423_184218/` and confirm the refinement transition matrix shows fewer `clingo_error → clingo_error` self-loops.

## docs/rewriter.md (to be written when out of plan mode)

Short version of this plan, written into `~/Projects/asp-arc-rewrites/docs/rewriter.md`:

```
# Deterministic ASP rewriter

## Goal
Fix common, mechanical syntax errors in LLM-emitted Clingo programs without
calling the LLM and without changing program logic.

## Shape
- ASPRewriter: stateless object, `rewrite(program: str) -> str`.
- Rule: small function, `apply(program: str) -> str`. One per fix.
- The rewriter loops over its rules until none changes the text.

## Plugged in
After code extraction, before Clingo verification:
    program = ASPRewriter().rewrite(program)
On a clean program it's a no-op.

## Starting rules
- mod_to_backslash:  `#mod` -> `\`
- strip_fences:      kill markdown fences, `<asp>`/`<clingo>` tags, leading prose
- agg_on_left:       `#agg{...} = X` -> `X = #agg{...}` for #count/#min/#max/#sum

## Testing
- tests/test_rewriter.py: per-rule (before, after) fixtures.
- scripts/evaluate_rewriter.py: walks every audit JSON under
  ~/Projects/asp-arc/src/audit/, reports per-rule fire count, total fixed,
  residual error breakdown, and appends to results/rewriter_progress.csv.
- scripts/plot_rewriter_progress.py: produces results/rewriter_progress.png
  (cumulative fixes vs. rule count, with two-word labels per rule).
- evaluate_rewriter.job: SLURM job (rome partition, .venv) that runs both
  scripts. Submit after every rule addition.

## Adding a rule
1. Write the smallest function that fixes one mechanical error.
2. Add (before, after) and (unchanged) unit tests.
3. Append it to ASPRewriter.
4. sbatch evaluate_rewriter.job. Keep the rule only if it fixes more programs
   than it regresses.
```
