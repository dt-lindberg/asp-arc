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

For each puzzle, `N_CANDIDATES` independent completions are requested in a single call (vLLM's `n` parameter) — they share the prefill, so it's much cheaper than `N_CANDIDATES` separate requests. Each candidate is validated independently in Step 3.

### Step 3 — Validate via Clingo
Extracts the ASP block from the model response (fails the run if 0 or 2+ blocks are found), then runs it against all 30 input-output grid pairs for that puzzle instance. For each pair:
- Injects `input(Row, Col, Color)` and `color(0..9)` facts.
- Runs Clingo with a 5-second timeout.
- Requires exactly 1 answer set whose `output/3` atoms match the expected grid exactly.

All 30 pairs must pass for the program to be considered correct.

### Output format
Passing and failing programs are both written to a JSONL file (`outputs/batch_<SLURM_JOB_ID>.jsonl`). One record per *candidate*, so each puzzle contributes `N_CANDIDATES` rows. The unique key is `(puzzle_name1, puzzle_name2, sid, candidate_index)`. Each record:

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
    "n_candidates": 4
  },
  "thinking": "...",
  "response": "...",
  "asp_code": "...",
  "validation": {
    "passed": true,
    "correct": 30,
    "total": 30,
    "clingo_errors": []
  }
}
```

Candidates whose response contained 0 or >1 ` ```asp ``` ` blocks are still written, with `asp_code: ""` and `validation: null`, so the raw `thinking`/response context is preserved and the key remains unique.

The source puzzle data (mixture summary, Python code) can always be reconstructed by looking up `(puzzle_name1, puzzle_name2, sid)` in the source parquet.

---

## What's next

- Run batch collection and evaluate per-candidate and per-puzzle pass rates.
- Potentially: a refinement loop that feeds Clingo error messages back to the model.

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
| `N_PUZZLES` | `10` | *(batch job only)* Number of unique puzzles to sample |
| `N_CANDIDATES` | `4` | *(batch job only)* Candidate ASP programs generated per puzzle (vLLM `n`) |

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
  batch_pipeline.py        batch entrypoint (steps 1–3, writes JSONL)
  main.py                  original smoke-test entrypoint
  config/
    config.py              general: seed, logging, Clingo settings
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

# Batch (10 puzzles by default)
sbatch jobs/run_batch_nvarc.job
N_PUZZLES=500 sbatch jobs/run_batch_nvarc.job

# Batch with custom parameters
N_PUZZLES=200 MAX_NUM_SEQS=20 REASONING_EFFORT=medium sbatch jobs/run_batch_nvarc.job
```
