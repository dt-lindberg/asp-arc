# ARC-AGI Prompting Pipeline (Adapted from LogicPuzzle Pipeline)

## Context

The current repo (`asp-gen-refinements`) implements a staged LLM+ASP pipeline that solves English-described LogicPuzzles by decomposing generation into 5 prompt stages (constants → predicates → search space → paraphrasing → constraints) plus a refinement loop over Clingo errors (syntax / UNSAT / multi-solution).

We are adapting this approach to ARC-AGI. The current ARC baseline is **one-shot ASP generation + Clingo retry on syntax/UNSAT errors**. The goal of this plan is a stepwise, refinement-driven pipeline conceptually parallel to the LogicPuzzle one, but reshaped for ARC's fundamentally different input (a few `(input_grid, output_grid)` example pairs + a test input) and a richer verification signal (pixel-level comparison against known training outputs).

Key constraints this plan is shaped by:
- Training examples give ground-truth outputs → **pixel-diff becomes a new refinement trigger** alongside syntax/UNSAT/multi.
- ARC has no English clues; a "transformation hypothesis" must be invented, not parsed.
- Perception (objects, symmetries, counts) is currently not handled explicitly in the baseline — the plan proposes a dedicated step for it.

## Conceptual Mapping

| LogicPuzzle stage                   | ARC analog                                                                            | Role                                                                                 |
| ----------------------------------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| (none)                              | **0. Perception**                                                                     | Deterministic feature extraction from each grid → ASP facts + human-readable summary |
| 2. Constants formatting             | **1. Vocabulary & constants**                                                         | Color palette, dims, object IDs — normalized into ASP-ready form                     |
| 3. Predicate generation             | **2. Predicate design**                                                               | Pick input/output grid predicates + auxiliary predicates for derived features        |
| 4. Search-space generation          | **3. Search-space generation**                                                        | Choice rules that frame the test output grid                                         |
| 5. Paraphrasing of English clues    | **4. Transformation hypothesis** (LLM observation)                                    | LLM verbalizes the rule observed across training examples                            |
| 6. Constraint-rule generation       | **5. Rule-to-ASP translation**                                                        | Translate hypothesis → ASP rules deriving `output_cell` from `input_cell`            |
| 7a/b/c refinement (syntax/unsat/multi) | **6a/b/c refinement (syntax/unsat/multi)**                                         | Same three triggers, same shape                                                      |
| (none)                              | **6d. Pixel-diff refinement** *(new, ARC-specific)*                                   | Generated rule runs on train inputs; mismatches vs. ground-truth outputs feed back   |

## Proposed Pipeline

### Stage 0 — Perception (deterministic, pre-LLM)

For every grid (all train I/O pairs + test input), compute structured features and emit both (a) ASP facts and (b) a short human-readable summary for prompt injection:

- Dimensions: `grid_size(Example, H, W).`
- Color palette used: `palette(Example, Col).`
- Connected components (per color, 4- or 8-connectivity): `object(Example, Oid, Col, Cells…)`, bounding boxes, sizes
- Background color (most common)
- Simple symmetries (horizontal/vertical/rotational) detected by equality check
- Counts (objects per color, unique colors)

These become a new placeholder `<PERCEPTION>` in downstream prompts and double as the initial fact layer of the final ASP program. This is the ARC analog of the `<CONSTANTS>` block — but generated programmatically, not from text.

*Reuse*: write a new module `perception.py`; it can reuse nothing from the current repo. Facts join the ASP program alongside the LLM-generated rules.

### Stage 1 — Vocabulary & constants (LLM)

Analog of step 2 (`prompts/2_constant_formatting.txt`). Inputs: `<PERCEPTION>`, raw grids.
Output: a normalized declaration block of constants: row/col index ranges, color constants, example IDs, object IDs. Purpose is formatting consistency for the predicates stage — small but preserves the "one LLM step per role" pattern.

This stage is optional if Stage 0 already outputs well-typed facts; I recommend skipping it in v1 and rolling its job into Stage 0's deterministic output. (Trim the pipeline where the LLM is adding no value.)

### Stage 2 — Predicate design (LLM)

Analog of step 3 (`prompts/3_gen_predicates.txt`). Inputs: `<PERCEPTION>`, training I/O pairs.
Output: predicate signatures the ASP program will use. Expected core set:

- `input_cell(Example, R, C, Col)`  — facts from perception
- `output_cell(Example, R, C, Col)` — facts (train) or derived (test)
- Auxiliary predicates the LLM judges useful: `object(Example, Oid, Col)`, `cell_of(Example, Oid, R, C)`, `neighbor/5`, `symmetric_axis/2`, etc.

Unlike LogicPuzzles, the predicate choice is part of what the LLM must *invent per task* based on the observed transformation — this stage is more consequential than its LogicPuzzle analog.

### Stage 3 — Search space (LLM)

Analog of step 4 (`prompts/4_gen_search_space.txt`). Inputs: predicates, test input dims, palette.
Output: ASP choice rules that instantiate every cell of the *test* output grid with exactly one color, e.g.

```asp
{ output_cell(test, R, C, Col) : color(Col) } = 1 :- row(test, R), col(test, C).
```

For training examples, `output_cell(train_i, R, C, Col)` is asserted as facts (not chosen) — see "ASP program structure" below.

### Stage 4 — Transformation hypothesis (LLM)

Analog of step 5 (paraphrasing). Inputs: `<PERCEPTION>`, training I/O pairs side-by-side.
Output: a natural-language, numbered description of the transformation rule, decomposed into sub-steps where possible (e.g., "1. Identify the largest non-background object. 2. Reflect it horizontally. 3. Place the reflection at the original location, overwriting the input.").

Same role as paraphrasing: normalize a messy observation into a bulleted, easily-translated spec before the ASP step. This is where chain-of-thought style reasoning belongs.

### Stage 5 — Rule-to-ASP translation (LLM, multi-turn)

Analog of step 6 (`prompts/6_gen_constraints.txt`). Inputs: predicates, hypothesis, perception facts, search space, training I/O grids.
Output: ASP rules that derive `output_cell(Example, R, C, Col)` from `input_cell(Example, R, C, Col)` **uniformly across all examples** — including the test input.

Keep the multi-turn in-context-examples format from the current constraints prompt: a system message locking the rule forms, followed by 1–2 hand-crafted `(task → ASP rules)` exemplars, then the current task. Exemplars should span archetypes (recoloring, symmetry, translation, object-counting).

### Stage 6 — Clingo execution

Assemble: `<PERCEPTION facts> + <search space> + <transformation rules>`, encode ASCII-safe, run Clingo. Reuse the existing runner in `pipeline.py:121-207` largely unchanged (same timeouts, same `--maxN 1001`, same status/answer-set return). Success criterion changes — see below.

### Stage 7 — Refinement loop

Reuse `refinement_loop.py`'s structure (batch by trigger kind, `MAX_ATTEMPTS` iterations, per-puzzle `done[]`). Add one new trigger:

- **`refinement_syntax`** — unchanged semantics; inputs: the current program + Clingo error list.
- **`refinement_semantic_unsat`** — unchanged.
- **`refinement_semantic_multi`** — still relevant (rule under-determined on test); but in ARC the dominant signal is pixel-diff, so this drops in priority.
- **`refinement_pixel_diff`** *(new)* — triggered when the program is satisfiable and unambiguous but the derived `output_cell(train_i, …)` disagrees with the known training output for at least one example. Feedback payload:
  - Per failing example: predicted grid, ground-truth grid, and a compact diff (list of `(R, C, predicted, actual)` tuples, truncated at a threshold analogous to `MAX_VARIABLE_ATOMS`).
  - Summary stats: which examples passed, which failed, total mismatched cells.
  - Prompt asks the LLM to revise the transformation rule (not the perception facts).

Priority ordering when multiple triggers apply: syntax → unsat → pixel-diff → multi. Pixel-diff implies the rule compiles and is non-vacuous, which is the most productive state to refine from.

## ASP Program Structure

Unify training and test inputs under a single `Example` argument so the same rules apply to both:

```asp
% --- Perception layer (deterministic facts, all examples) ---
input_cell(train1, 0, 0, 3). input_cell(train1, 0, 1, 0). ...
input_cell(test,   0, 0, 5). ...

% --- Ground-truth training outputs as facts (used for pixel-diff, not as constraints) ---
actual_output_cell(train1, 0, 0, 3). ...

% --- Search space: only the test output is chosen ---
{ output_cell(test, R, C, Col) : color(Col) } = 1 :- row(test, R), col(test, C).

% --- LLM-generated transformation rules (fire on any Example) ---
output_cell(E, R, C, Col) :- input_cell(E, R, C, Col), ...conditions...

#show output_cell/4.
```

Notes:
- **Do NOT** add `:- output_cell(E,R,C,C1), actual_output_cell(E,R,C,C2), C1!=C2.` Doing so would turn pixel-diff failures into UNSAT and destroy the rich cell-level feedback signal. Instead, check training-example agreement in Python after solving.
- The test output is "searched" (choice rule); the training outputs are *derived* by the rules — if the rules are correct, they produce the right training outputs without any search. The choice rule exists so Clingo has a formal answer-set space for the test case even when no rule fires on a cell.

## Verification

Success = (status is None) ∧ (≥1 answer set) ∧ (on some chosen answer set, `output_cell(train_i, …)` matches `actual_output_cell(train_i, …)` for *all* training examples). The test prediction is the `output_cell(test, …)` grid from that answer set. If multiple answer sets exist, prefer one that passes all training examples; fall back to first.

## Critical Files / Reuse Points

From this repo, reusable as-is or with minor edits:
- `vllm_engine.py` — LLM batch inference layer, unchanged.
- `pipeline.py:121-207` — Clingo runner (timeouts, threading, ASCII safety).
- `pipeline.py:58-119` — batched prompt substitution + multi-turn constraints helper (adapt for ARC prompts).
- `refinement_loop.py` — loop skeleton, audit hooks, `MAX_ATTEMPTS`, batch-by-kind routing. Add one new kind.
- `audit.py` — per-puzzle JSON audit; extend schema with `perception`, `hypothesis`, and a `pixel_diff` block per refinement attempt.
- `utils.py:4-18` — `extract_code_blocks()`.
- `config.py` — sampling params, `MAX_ATTEMPTS`, etc. Add: `PIXEL_DIFF_MAX_CELLS` (cap diff payload size).

New modules required:
- `perception.py` — grid → structured facts + human-readable summary.
- `grid_verifier.py` — compare derived vs. actual training outputs, emit diff payloads.
- `prompts/arc/*.txt` — new prompt templates, one per stage, following the `<PLACEHOLDER>` convention already in use.

## Recommendation Around the Perception Gap

The user flagged perception as "currently not done explicitly." Recommendation: **add Stage 0 deterministic perception first**, before adding the LLM hypothesis step. A deterministic layer is cheap, has zero variance, and gives every downstream prompt a grounded fact base. Once perception facts exist, the LLM's hypothesis step in Stage 4 becomes the natural place to reason about *which* perceived features the transformation uses — keeping the perception/hypothesis responsibilities clean and separate.

Start with a small feature set in `perception.py` (dims, palette, connected components, background, axial symmetries). Add more features only when a failure-mode analysis shows they would have helped.

## Verification of This Design End-to-End

Once implemented:
1. Run the pipeline on a small ARC development slice (~10 tasks) with `MAX_ATTEMPTS=0` to baseline initial generation quality without refinement.
2. Enable refinement; inspect audit JSONs for per-trigger distribution — confirm `pixel_diff` is firing and improving outcomes.
3. Ablate Stage 0 (perception facts disabled) to confirm it's adding value.
4. Ablate Stage 4 (LLM hypothesis) to check whether direct Stage 5 rule generation is competitive.
5. Compare task-level pass rate against the current one-shot-plus-retry baseline on the same dev slice.

## Open Questions / Suggestions

- **Choice-rule scope**: should `output_cell(test, …)` be fully chosen, or constrained to the palette observed in training? The latter is a cheap prior that usually holds.
- **Program-per-example vs. unified**: the proposed structure solves once over all examples. An alternative is to run the same rule program separately per example (test + each train) and verify agreement in Python. Cheaper and easier to debug; lose some ASP-level factoring. Worth benchmarking.
- **Hypothesis stage as free-form vs. constrained**: the paraphrasing prompt in LogicPuzzles imposes a strict rewriting schema. ARC hypotheses are more open-ended; consider a lightly-structured template ("Objects observed: …  Transformation: step 1 …; step 2 …") to keep Stage 5 translatable.
