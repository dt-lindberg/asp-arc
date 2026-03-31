---
name: inspect-run
description: Inspect the output of an asp-arc pipeline run. Use when asked to check results, debug a run, or evaluate the quality of LLM-generated ASP programs. Loads outputs/<run_id>.json and surfaces key information: prompts sent, LLM thinking, generated programs, Clingo verification results, and grid diffs.
---

# Skill: inspect-run

Use this skill to examine a completed asp-arc pipeline run.

## Run output location

All run outputs are in `outputs/<run_id>.json` where `run_id` is a `YYYYMMDD_HHMMSS` timestamp.

## JSON structure per puzzle

```json
{
  "run_id": "20260330_120000",
  "puzzle_id": "007bbfb7",
  "dataset": "arc-v1-training",
  "n_train_examples": 5,
  "steps": {
    "analysis":     {"prompt": "...", "thinking": "...", "response": "...", "extracted": "..."},
    "predicates":   {"prompt": "...", "thinking": "...", "response": "...", "extracted": "..."},
    "choice_rules": {"prompt": "...", "thinking": "...", "response": "...", "extracted": "..."},
    "constraints":  {"prompt": "...", "thinking": "...", "response": "...", "extracted": "..."}
  },
  "full_program": "...",
  "train_verifications": [
    {
      "example_idx": 0,
      "status": "correct|wrong_values|unsatisfiable|underconstrained|clingo_error|shape_mismatch",
      "n_answer_sets": 1,
      "clingo_errors": "",
      "correct": true,
      "diff": "0 7 7\n7 7 7\n0 7 7",
      "accuracy": 1.0,
      "grid_predicted": [[...]],
      "grid_expected": [[...]]
    }
  ],
  "all_train_correct": false,
  "refinements": [
    {
      "attempt": 1,
      "prompt": "...",
      "thinking": "...",
      "response": "...",
      "program": "...",
      "train_verifications": [...],
      "all_train_correct": false
    }
  ],
  "final_correct": false
}
```

## Useful inspection patterns

```python
import json

with open("outputs/<run_id>.json") as f:
    records = json.load(f)

for r in records:
    print(r["puzzle_id"], "→", "SOLVED" if r["final_correct"] else "UNSOLVED")
    # Inspect generated program
    print(r["full_program"])
    # Inspect per-step thinking
    print(r["steps"]["constraints"]["thinking"])
    # Check training example diffs
    for v in r["train_verifications"]:
        print(f"  Example {v['example_idx']}: {v['status']}, acc={v['accuracy']}")
        if v["diff"]:
            print(v["diff"])
    # Check refinement attempts
    for ref in r["refinements"]:
        print(f"  Refinement {ref['attempt']}: all_correct={ref['all_train_correct']}")
```

## What to look for

- **Prompts**: Are the training examples formatted correctly? Is the accumulated context (constants → predicates → ...) coherent?
- **Thinking traces**: Does the LLM reason about the right aspects of the transformation?
- **Extracted programs**: Are they syntactically valid ASP? Do they use `input(R,C,Color)` without defining it?
- **Clingo errors**: Parse errors indicate syntax issues; underconstrained means constraints are too weak; unsatisfiable means too strong.
- **Grid diffs**: The `pred/expected` format shows exactly which cells are wrong — useful for diagnosing constraint errors.
- **Refinement quality**: Does the LLM fix the specific issues called out in the feedback?
