# Status

## Current State

A first working draft of the full pipeline is in place. The system loads ARC-AGI puzzles, runs a 4-step decomposed LLM generation (analysis → predicates → choice rules → constraints), verifies the resulting ASP program against training examples using Clingo, and iterates with a refinement loop.

All outputs are saved as structured JSON in `outputs/<run_id>.json`.

---

## What Works

- **4-step pipeline**: batched LLM generation (analysis, predicates, choice rules, constraints) with prompt caching
- **Clingo evaluation**: per-example verification with grid diff, accuracy, and error reporting
- **Refinement loop**: up to `MAX_ATTEMPTS` reattempt iterations with full history context
- **Structured JSON output**: all prompts, thinking traces, programs, and verification results persisted
- **Logging**: timestamped log files under `logs/`

---

## In Progress

- **Code cleanup**: minor refactoring and polish pass
- **Clingo error handling**: programs with syntax errors now skip per-example evaluation and report errors once (previously all examples were evaluated redundantly)
- **Evaluation / Streamlit app**: interactive inspector for browsing run outputs, visualising puzzle grids, and inspecting step-by-step reasoning (see `eval/app.py`)

---

## Known Issues / Next Steps

- LLM currently produces frequent syntax errors; understanding the error patterns and tightening the prompts is the immediate priority
- The refinement loop does not yet improve syntax errors reliably — the feedback is correct but the LLM tends to repeat the same mistakes
- No aggregate evaluation metrics yet (solve rate across many puzzles)
