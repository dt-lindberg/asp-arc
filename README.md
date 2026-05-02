# asp-arc-gpt-oss-nvarc

Pipeline for generating a gold-standard `(ARC puzzle → ASP program)` SFT
corpus by translating NVARC's Python solutions into ASP with GPT-OSS-120B,
then Clingo-verifying each candidate.

**Status: v1 skeleton — smoke test only.** The vLLM engine for
`openai/gpt-oss-120b` is wired up and `src/main.py` runs a single toy prompt
end-to-end. NVARC ingestion, multi-candidate diversified prompting, and
Clingo verification are not yet implemented.

## Layout

```
jobs/                    SLURM scripts (install_env.job, run.job)
docs/                    design notes
outputs/                 run artefacts (JSONL of verified pairs, eventually)
slurm_logs/              SLURM stdout/stderr
src/
  main.py                smoke-test entrypoint
  llm/                   vLLM engine + helpers
  config/                config.py (general) + config_llm.py (model-specific)
  prompts/               prompt templates
  utils/                 shared helpers (logger, …)
  requirements_vllm.txt
  requirements_clingo_etal.txt
```

## Running

```bash
# One-time: build venv and download GPT-OSS-120B weights
sbatch jobs/install_env.job

# Smoke test on 1× H100 (bump --gpus in the script for tensor parallelism)
sbatch jobs/run.job
```
