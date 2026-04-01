# Experiment: Single-Prompt Reasoning Pipeline

## Goal

Replace the 4-step decomposed prompt pipeline with a single prompt designed for a reasoning model. Instead of driving the model through analysis → predicates → choice_rules → constraints via four sequential LLM calls, the model receives the puzzle examples and a single prompt with guidance on what to attend to. The model is expected to do all decomposition internally through its chain-of-thought reasoning.

The refinement loop is unchanged: failed programs are re-submitted with accumulated Clingo feedback appended, exactly as in the current pipeline.

**Why:** The hypothesis is that a capable reasoning model can produce an equivalent or better ASP program in one shot, and the multi-step structure of the current pipeline is scaffolding that adds latency without necessarily adding quality.

## Model: Nemotron-Cascade-2

The target model for this experiment is `chankhavu/Nemotron-Cascade-2-30B-A3B-NVFP4` (NVFP4 quantized).

### Where to find the Nemotron setup

- `scripts/nemotron-exp/test.py` — standalone vLLM test script, shows the correct LLM init params and thinking/non-thinking mode usage
- `scripts/nemotron-exp/run.job` — SLURM job for running the test on `gpu_h100`
- `scripts/nemotron_cascade_2.py` — earlier standalone test using AWQ-INT4 variant (ignore — use NVFP4)

### Critical Nemotron init parameters (copy from test.py)

```python
LLM(
    model="chankhavu/Nemotron-Cascade-2-30B-A3B-NVFP4",
    trust_remote_code=True,
    mamba_ssm_cache_dtype="float32",   # required — prevents gibberish from Mamba blocks
    kv_cache_dtype="fp8",
    max_model_len=8192,
    tensor_parallel_size=1,
)
```

Thinking mode is activated by default. Non-thinking mode uses `chat_template_kwargs={"enable_thinking": False}` in `llm.chat()`. The model uses `llm.chat()` (not `llm.generate()` with a manually applied template).

## Environment

- **Cluster:** Snellius (SLURM), partition `gpu_h100`
- **Venv:** `.venv/` is already configured with all dependencies (vLLM, clingo, etc.)
  - Activate with: `source .venv/bin/activate`
  - Do NOT run `sbatch scripts/copy_venv.job` — the venv is already in place
- **Working directory for all jobs:** `/home/dlindberg/Projects/asp-arc`
- **Module loads required in job scripts:**
  ```
  module purge
  module load 2025
  module load CUDA/12.8.0
  module load Python/3.13.5-GCCcore-14.3.0
  ```

## Current pipeline (for reference)

Read these files to understand what exists before making changes:

- `main.py` — entry point; runs 4 steps then a refinement loop
- `pipeline.py` — LLM + Clingo wrapper; `gen_response_batch()` and `gen_response_raw_batch()`
- `vllm_engine.py` — vLLM engine for Qwen3; uses `llm.generate()` with chat template applied manually
- `config.py` — all constants including `PROMPT_PATHS`, model path, sampling params
- `prompts/1_analysis.txt` through `prompts/5_reattempt.txt` — the five prompt templates
- `eval.py` — `verify_on_training_examples()`, `build_train_feedback()`, `all_correct()`
- `utils.py` — `format_examples_for_prompt()`, `extract_code_blocks()`
- `run.job` — SLURM job (currently Qwen3 on `gpu_a100`, `--num 3`)

## What to implement

### 1. New prompt file: `prompts/single_step.txt`

A single prompt template with one placeholder `<EXAMPLES>`. It should:
- Set the context (ARC-AGI, ASP encoding, what `input/3`, `color/1`, and `output/3` mean)
- Show the puzzle examples
- Give reasoning hints: think about what constants exist, what predicates to define, write a choice rule for candidate output cells, then add integrity constraints to rule out wrong candidates
- Ask for a complete ASP program in a single ` ```asp ``` ` code block
- Mirror the style and constraints of the existing prompts (no `input/3` facts, no `color/1` facts, output is `output(Row, Col, Color)`)

### 2. New vLLM engine: `nemotron_engine.py`

Adapt `vllm_engine.py` for Nemotron. Key differences:
- Uses `llm.chat()` instead of `llm.generate()` (Nemotron requires chat API)
- Init params as listed above
- Thinking mode on by default (set `enable_thinking=True` or omit the flag)
- Thinking output is inside `<think>...</think>` tags — reuse the existing `_split_thinking()` logic from `vllm_engine.py`
- Expose the same interface: `generate_batch(messages_list)` → list of `(thinking, response)` tuples

### 3. New entry point: `main_single.py`

Simplified version of `main.py` that:
- Skips steps 1–4 entirely
- Makes one LLM call per puzzle using the `single_step` prompt with `<EXAMPLES>` filled in
- Extracts the ASP program with `extract_code_blocks()`
- Records the result in the same JSON schema as `main.py` (use a `steps` dict with a single `"generation"` key)
- Runs the same refinement loop (`prompts/5_reattempt.txt` with `===SEPARATOR===` split, `_build_reattempt_prompt()`, `gen_response_raw_batch()`)
- Saves to `outputs/<run_id>.json`

### 4. New SLURM job: `run_single.job`

Copy `scripts/nemotron-exp/run.job` as a base (uses `gpu_h100`). Change:
- `--job-name` to `ASPArcSingle`
- `--chdir` to `/home/dlindberg/Projects/asp-arc`
- `--output` to `outputs/slurm_single_%A.out`
- Script body: activate venv, run `python3 main_single.py --dataset arc-v1-training --num 3`

## How to test

1. Submit the job: `sbatch run_single.job`
2. Monitor: `squeue -u dlindberg` (check job state)
3. Check SLURM output: `outputs/slurm_single_<jobid>.out`

**The pipeline works correctly if:**
- The job runs to completion without crashing
- A file `outputs/<run_id>.json` is created
- Each puzzle record contains:
  - A non-empty `steps.generation.response`
  - A non-empty `full_program` (ASP code was extracted)
  - `train_verifications` with at least one entry per puzzle
  - No universal `clingo_error` across all examples on all puzzles (some errors are expected on first attempt)
- The refinement loop runs for puzzles that don't pass on the first attempt
- Final log line reports `Final: X/3 puzzle(s) solved`

**To inspect results in detail**, use the `inspect-run` skill in Claude Code:
```
/inspect-run
```
It loads `outputs/<run_id>.json` and surfaces prompts, thinking traces, generated programs, and Clingo diffs.

## Success criteria for this experiment

The goal is not to maximize solve rate — it is to verify that:
1. The single-prompt approach produces valid, runnable ASP programs (not just syntax errors)
2. The Nemotron thinking trace shows reasoning about the puzzle structure
3. The refinement loop correctly picks up failures and improves the program across attempts
