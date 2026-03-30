---
name: Architecture
description: Overview of the asp-arc pipeline — modules, data flow, key decisions, and how to run it.
---

# asp-arc Architecture

## Overview

`asp-arc` solves ARC-AGI puzzles by having a local LLM (Qwen3-30B-A3B) generate Answer Set Programming (ASP) code that encodes the grid transformation rule. The pipeline is organised around the central hypothesis:

> *Extracting constants, then predicates, then choice rules, and lastly constraints boosts the LLM's ability to solve ARC puzzles.*

## Module Map

```
main.py                   # CLI entry point — orchestrates the full pipeline
pipeline.py               # LLM calls (vLLM), Clingo runner, prompt caching
arc_loader.py             # Load ARC-AGI puzzle JSONs, format for prompts
eval.py                   # Verify programs on training examples, build feedback
utils.py                  # Grid formatting, extract_code_blocks, grid diff
logger.py                 # Logging setup (stdout + logs/<date>/log_*.log)
vllm_engine.py            # vLLM batched inference wrapper (Qwen3-30B-A3B)
think_logits_processor.py # Forces </think> after the thinking budget is exhausted
config.py                 # All configuration: model path, inference params, paths
prompts/		  # All prompts, defining each step
```

## Data Flow

```
main.py
  │
  ├── load N puzzles (arc_loader.py)
  │     └── format each puzzle's train examples as text diagrams (utils.py)
  │
  ├── STEP 1 — transformation (batched LLM call)
  │     Prompt: <EXAMPLES>
  │     Output: <ANALYSIS> describing the puzzle in natural language
  │
  ├── STEP 2 — predicates (batched LLM call)
  │     Prompt: <EXAMPLES> + <ANALYSIS>
  │     Output: auxiliary predicates defined in natural language
  │
  ├── STEP 3 — choice rules (batched LLM call)
  │     Prompt: <EXAMPLES> + <ANALYSIS> + <PREDICATES>
  │     Output: output_cell domain + 1{output(R,C,Color):color(Color)}1 rule
  │
  ├── STEP 4 — constraints (batched LLM call)
  │     Prompt: <EXAMPLES> + <ANALYSIS> + <PREDICATES> + <CHOICE_RULES>
  │     Output: :- integrity constraints encoding the transformation
  │
  ├── Assemble full program (constants + predicates + choice_rules + constraints)
  │
  ├── Verify on training examples (eval.py, sequential Clingo)
  │     For each example: prepend input(R,C,Color) facts → run Clingo
  │       → extract output(R,C,Color) atoms → compare to expected grid
  │       → produce diff string (pred/expected for wrong cells)
  │
  └── Refinement loop (up to MAX_ATTEMPTS times)
        For each unsolved puzzle:
          build reattempt prompt (examples + history of attempts + feedback)
          → batched LLM call → new program → Clingo verify → repeat
```

## Fixed ASP Encoding

The colors are always `color(0;1;2;3;4;5;6;7;8;9)`.

The output predicate and choice rule are always:
```asp
output_cell(R, C) :- output_rows(Rows), output_cols(Cols), R = 0..Rows-1, C = 0..Cols-1.
1 { output(R, C, Color) : color(Color) } 1 :- output_cell(R, C).
```

Input facts are always injected externally by `eval.py` / `main.py`:
```asp
input(0,0,7). input(0,1,0). ...
```

The LLM must **not** generate `input/3` facts — this is explicitly stated in every prompt.

## Data Storage

Each run produces `outputs/<run_id>.json`, a list of puzzle records with:
- `puzzle_id`, `dataset`, `n_train_examples`
- `steps`: per-step `{prompt, thinking, response, extracted}`
- `full_program`: assembled ASP program
- `train_verifications`: per-example `{status, n_answer_sets, correct, diff, accuracy, grid_predicted, grid_expected}`
- `refinements`: list of reattempt records (attempt, prompt, thinking, response, program, train_verifications)
- `all_train_correct`, `final_correct`

## How to Run

### Prerequisites
1. Copy venv from asp-gen-refinements (once):
   ```
   sbatch copy_venv.job
   ```

2. Submit the main job:
   ```
   sbatch run.job
   ```

### Quick test (login node, CPU-only, small program):
```bash
source .venv/bin/activate
python3 main.py --dataset arc-v1-training --num 3
```

### Specific puzzles:
```bash
python3 main.py --puzzle_ids 007bbfb7 0ca9ddb6 0d3d703e
```

## Caching

LLM responses are cached in `caches/vllm_<engine>_<step>.json`, keyed by the exact prompt string. Re-running with the same puzzle and prompts skips LLM calls entirely.

## Refinement Logic

The refinement loop differs from the logic-puzzle predecessor:
- **Termination condition**: all training examples pass (exact grid match), not "exactly 1 answer set"
- **Feedback content**: per-example grid diff + Clingo errors (underconstrained, unsatisfiable, parse error)
- **History**: all prior attempts + their feedback are included in the reattempt prompt
- **Max attempts**: `MAX_ATTEMPTS` from config.py (default: 5)

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Input fact injection | External, not LLM-generated | LLM generates a general program; harness injects example-specific facts for verification |
| Output predicate | Fixed as `output(Row, Col, Color)` | Deterministic extraction from answer sets |
| Verification trigger | Any training example wrong | Stronger signal than just "# answer sets = 1" |
| Batching | All puzzles per step in one vLLM batch | Maximises GPU throughput |
| Caching | Prompt-keyed JSON files | Avoids redundant LLM calls across runs |
| Logs | `logs/<date>/log_<timestamp>.log` + stdout | Easy to find, doesn't pollute root |
