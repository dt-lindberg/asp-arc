# Deterministic ASP rewriter

## Goal
Fix common, mechanical syntax errors in LLM-emitted Clingo programs without
calling the LLM and without changing program logic.

## Shape
- `ASPRewriter`: stateless object, `rewrite(program: str) -> str`.
- `Rule`: small function, `apply(program: str) -> str`. One per fix.
- The rewriter loops over its rules until none changes the text.

## Plug-in (later)
After code extraction, before Clingo verification:

    program = ASPRewriter().rewrite(program)

On a clean program it is a no-op.

## Rule catalogue (in evaluation order)
- `strip_fences` — drop ``` ``` ```/`<asp>`/`<clingo>` wrappers and any
  leading prose lines.
- `strip_html_tags` — drop leftover XML-ish wrappers that `strip_fences`
  could not collapse (e.g. unmatched `<asp>` only).
- `agg_parens_to_braces` — `#count(...)` → `#count{...}` and friends.
- `agg_on_left` — `#agg{...} = X` → `X = #agg{...}` (operator flipped
  for `<`/`>`/`<=`/`>=`).
- `mod_to_backslash` — `#mod` → `\` (Clingo's modulo).
- `lowercase_predicates` — `Foo(...)` → `foo(...)` (Clingo treats leading
  uppercase as variables).
- `swi_aggregate` — `aggregate_all(count, Goal, N)` → `N = #count{1, Goal : Goal}`.
- `bare_arith_assignment` — top-level `H = expr.` → `h(H) :- H = expr.`.
- `strip_continuation_colon` — drop a stray `:-` at the start of a body
  continuation line.

## Testing
- `tests/test_rewriter.py`: per-rule (before, after) and (unchanged)
  fixtures, no Clingo required.
- `scripts/evaluate_rewriter.py`: walks every audit JSON under
  `~/Projects/asp-arc/src/audit/`, extracts the unique programs that
  produced `clingo_error` in any training-example verification, runs each
  through `ASPRewriter().rewrite(...)` and Clingo, and reports per-rule
  fire count, total fixed, regressions, and the top residual errors.
- `scripts/plot_rewriter_progress.py`: produces
  `results/rewriter_progress.png` (cumulative fixes vs. rule count, with
  per-rule short labels).
- `evaluate_rewriter.job`: SLURM job (rome partition, shared `.venv`)
  that runs both scripts.

## Adding a rule
1. Write the smallest function that fixes one mechanical error.
2. Add `(before, after)` and `(unchanged)` unit tests.
3. Append it to the `ALL_RULES` registry in `src/utils/rewriter.py`.
4. `SWEEP=rule_a,rule_b sbatch evaluate_rewriter.job` (rerun the whole
   sweep so the CSV stays consistent).  Keep the rule only if it fixes
   more programs than it regresses.
