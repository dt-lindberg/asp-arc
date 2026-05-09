# asp-arc-gpt-oss-nvarc

Pipeline for generating a gold-standard `(ARC puzzle → ASP program)` SFT corpus
by translating NVARC's Python solutions into ASP with GPT-OSS-120B, then
Clingo-verifying each candidate.

**Status: batch pipeline implemented, corpus collection not yet started.**

---

## What works

### Step 1 — Load puzzle data
Samples puzzle rows from the NVARC `outputs/` parquets on scratch (`/scratch-shared/dlindberg/nvarc_data/`). Each row supplies:
- **Mixture summary** — the `<puzzle>` XML block from the prompt field (rules, input generation logic, solution steps, key insight, concepts).
- **Python solution** — the `generate_puzzle_output()` function extracted from the completion field.

Batch mode uses a weighted random sampler across all 150 parquet files, reading only parquet footer metadata to determine file sizes. Each file is loaded at most once. Duplicate puzzles (by `puzzle_name1/puzzle_name2`) are skipped, so slightly fewer than N results may be returned.

### Step 2 — Prompt & generate ASP
Builds a translation prompt from a template (`prompts/nvarc_asp_translation.txt`) and sends it to GPT-OSS-120B via the vLLM server. The model is instructed to output a single ` ```asp ``` ` block and to write idiomatic ASP rather than a line-by-line Python translation.

One generation per puzzle. Puzzles are processed in chunks of `MAX_NUM_SEQS`, all of which are sent to vLLM in a single batched call so they share the server's continuous batching.

### Step 3 — Validate via Clingo
Extracts the ASP block from the model response, then runs it against all 30 input-output grid pairs for that puzzle instance. For each pair:
- Injects `input(Row, Col, Color)` and `color(0..9)` facts.
- Runs Clingo with a 5-second timeout.
- Requires exactly 1 answer set whose `output/3` atoms match the expected grid exactly.

All 30 pairs must pass for the program to be considered correct.

### Step 4 — Refinement loop
Puzzles whose initial generation fails (Clingo error, UNSAT, multiple answer sets, wrong cells, or no extractable ` ```asp ``` ` block) enter a refinement loop. For up to `MAX_REFINEMENT_ATTEMPTS` rounds, the failure is fed back to the model as a follow-up user turn:

- The original system/user prompt is replayed verbatim — the model still sees the syntax guide and Python source on every turn.
- The full assistant↔user back-and-forth from prior rounds is included so the model sees its own program evolve.
- The new user turn carries a category-specific feedback message (Clingo error / UNSAT / multi-answer / grid diff with line-numbered code), or a NO_BLOCK message acknowledging that the cause may be either malformed output or `max_tokens` exhaustion during reasoning.
- Reasoning traces (`thinking`) are **never** replayed as assistant content — only the visible response is. This avoids a runaway prompt that would exceed `max-model-len` when the previous round burned its full token budget on reasoning.

All unsolved puzzles in a chunk are batched into a single vLLM call per refinement round. The first round that produces a passing program ends refinement for that puzzle.

### Output format
Records for every generation (initial and each refinement round) are appended to a JSONL file (`outputs/batch_<SLURM_JOB_ID>.jsonl`). The unique key is `(puzzle_name1, puzzle_name2, sid, refinement_round)`. Each record:

```json
{
  "puzzle_name1": "00576224",
  "puzzle_name2": "84db8fc4",
  "sid": 3,
  "candidate_index": 0,
  "source_parquet": "data-00007.parquet",
  "prompt_template": "prompts/nvarc_asp_translation.txt",
  "parameters": {
    "model": "openai/gpt-oss-120b",
    "reasoning_effort": "high",
    "temperature": 1.0,
    "top_k": -1,
    "max_tokens": 80000,
    "n_candidates": 1
  },
  "thinking": "...",
  "response": "...",
  "asp_code": "...",
  "validation": {
    "passed": true,
    "correct": 30,
    "total": 30,
    "clingo_errors": []
  },
  "refinement_round": 0
}
```

Refinement records (`refinement_round >= 1`) carry four extra fields describing what triggered the round and what feedback was sent:

- `trigger` — one of `syntax_error` / `unsat` / `multi_answer_set` / `wrong_cells` / `no_block`.
- `first_failing_pair_index` — index of the first failing input/output pair (or `-1` for `no_block`).
- `feedback_prompt` — the literal user-turn message sent into the model.
- `grid_diff` — for `wrong_cells`, the list of `{row, col, expected, got}` cells that disagree (else `null`).

Generations that did not yield a parseable ` ```asp ``` ` block are still written, with `asp_code: ""` and `validation: null`. The next refinement round (if any) will use the NO_BLOCK feedback path.

`candidate_index` and `parameters.n_candidates` are pinned to `0` / `1` for schema stability with the older multi-candidate pipeline (see `src/archived/`).

The source puzzle data (mixture summary, Python code) can always be reconstructed by looking up `(puzzle_name1, puzzle_name2, sid)` in the source parquet.

To find the final verdict for a puzzle, take the record with the largest `refinement_round` for that `(puzzle_name1, puzzle_name2, sid)`. If `validation.passed` is `true` there, the puzzle was solved.

---

## What's next

- Run batch collection and evaluate solve rates by initial-vs-refinement.
- Retroactive Step 5: for puzzles whose final record is still failing, re-feed them through the pipeline from scratch (cheaply identifiable from the JSONL).

---

## Configuration

All tuneable parameters are read from environment variables. The SLURM jobs export defaults; override any of them on the `sbatch` command line.

| Variable | Default | Description |
|---|---|---|
| `MAX_NUM_SEQS` | `8` / `20` | Concurrent sequences — sets both the vLLM `--max-num-seqs` and the client semaphore |
| `REASONING_EFFORT` | `high` | GPT-OSS reasoning budget: `low`, `medium`, or `high` |
| `VLLM_HOST` | `127.0.0.1` | vLLM server host |
| `VLLM_PORT` | `8001` | vLLM server port |
| `TP_SIZE` | `$SLURM_GPUS` | Tensor parallelism — set automatically from `--gpus` |
| `LOG_LEVEL` | `debug` | Logging verbosity: `debug`, `info`, `warning`, `error` |
| `LOG_DIR` | `logs/` | Directory for log files |
| `N_PUZZLES` | `50` | *(batch job only)* Number of unique puzzles to sample |
| `MAX_REFINEMENT_ATTEMPTS` | `2` | *(batch job only)* Refinement rounds per failed puzzle (`0` disables refinement) |

---

## Layout

```
jobs/
  install_env.job          one-time: build venv + download GPT-OSS-120B weights
  run.job                  smoke test (single toy prompt)
  run_nvarc.job            single-puzzle pipeline (steps 1–3)
  run_batch_nvarc.job      batch pipeline (N puzzles, writes JSONL)
docs/                      design notes
outputs/                   JSONL results from batch runs
slurm_logs/                SLURM stdout/stderr
src/
  nvarc_pipeline.py        single-puzzle entrypoint (steps 1–3, prints to stdout)
  batch_pipeline.py        batch entrypoint (steps 1–4, writes JSONL)
  main.py                  original smoke-test entrypoint
  archived/
    batch_pipeline.py      previous multi-candidate pipeline (kept for reference)
  config/
    config.py              general: seed, logging, Clingo, refinement settings
    config_llm.py          model & vLLM server settings (MAX_NUM_SEQS from env)
    config_nvarc.py        NVARC data paths & prompt template path
  llm/
    vllm_engine.py         async vLLM client (OpenAI-compatible)
  prompts/
    nvarc_asp_translation.txt  prompt template for Python→ASP translation
    smoke_test.txt
    math_test.txt
  utils/
    nvarc_data.py          load rows from outputs/ parquets + grid JSON pairs
    nvarc_sampler.py       weighted random sampler across all parquets
    nvarc_formatting.py    extraction (puzzle XML, Python code, ASP block) + fact generation
    asp_validator.py       orchestrates Clingo validation across all grid pairs
    clingo_runner.py       Clingo ground/solve with threading timeouts
    refinement.py          failure categorization + feedback message builders
    output_writer.py       append-only JSONL writer
    logger.py
  requirements_vllm.txt
  requirements_clingo_etal.txt
```

## Running

```bash
# One-time: build venv and download GPT-OSS-120B weights (~80 GB)
sbatch jobs/install_env.job

# Single puzzle (row 0 by default)
sbatch jobs/run_nvarc.job
ROW_INDEX=5 sbatch jobs/run_nvarc.job

# Batch (50 puzzles by default; one generation + up to 2 refinement rounds each)
sbatch jobs/run_batch_nvarc.job
N_PUZZLES=500 sbatch jobs/run_batch_nvarc.job

# Batch with custom parameters
N_PUZZLES=200 MAX_NUM_SEQS=20 REASONING_EFFORT=medium sbatch jobs/run_batch_nvarc.job
MAX_REFINEMENT_ATTEMPTS=3 N_PUZZLES=200 sbatch jobs/run_batch_nvarc.job
MAX_REFINEMENT_ATTEMPTS=0 sbatch jobs/run_batch_nvarc.job   # initial-only, no refinement
```
