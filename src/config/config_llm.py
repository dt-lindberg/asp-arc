"""GPT-OSS-120B / vLLM configuration.

Client-side settings (model id, sampling params, reasoning effort) and
server-side defaults (documented here so SLURM scripts stay in sync).

General settings (seed, logging) live in config.py.
"""

import os

# HuggingFace repo id; weights are pulled into $HF_HUB_CACHE on first run.
MODEL_REPO_ID = "openai/gpt-oss-120b"

# vLLM server address — set via env or defaults to localhost.
VLLM_HOST = os.environ.get("VLLM_HOST", "127.0.0.1")
VLLM_PORT = int(os.environ.get("VLLM_PORT", "8000"))

# Reasoning effort: "low" / "medium" / "high".
# GPT-OSS exposes this via the chat template; it replaces Nemotron's
# enable_thinking toggle.
REASONING_EFFORT = os.environ.get("REASONING_EFFORT", "high").strip().lower()

# ┌─────────────────────────────┬──────────────────────────────────────────────────┐
# │ Client-side parameters      │ Description                                      │
# ├─────────────────────────────┼──────────────────────────────────────────────────┤
# │ MAX_TOKENS                  │ max output tokens/seq = think budget + response  │
# │ MAX_NUM_SEQS                │ max concurrent in-flight requests (semaphore)    │
# │ TEMPERATURE                 │ sampling temperature; 0 = greedy                 │
# │ TOP_P                       │ nucleus sampling probability mass cutoff         │
# │ TOP_K                       │ vocab truncation to top-k before sampling        │
# ├─────────────────────────────┼──────────────────────────────────────────────────┤
# │ Server-side (for SLURM)     │ documented here as single source of truth        │
# ├─────────────────────────────┼──────────────────────────────────────────────────┤
# │ MAX_MODEL_LEN               │ max input+output tokens per sequence (KV slot)   │
# │ MAX_NUM_BATCHED_TOKENS      │ max input tokens across batch per scheduler step │
# │ GPU_MEMORY_UTILIZATION      │ fraction of VRAM reserved for KV-cache           │
# └─────────────────────────────┴──────────────────────────────────────────────────┘
MAX_TOKENS = 80_000
MAX_NUM_SEQS = 8
TEMPERATURE = 1.0
TOP_P = 1.0
TOP_K = -1  # disabled

# Server-side defaults (used by serve_vllm.job; kept here for reference).
MAX_MODEL_LEN = 100_000
MAX_NUM_BATCHED_TOKENS = 4_096
GPU_MEMORY_UTILIZATION = 0.92

# Tensor parallelism — read from env so the SLURM script can pass
# `TP_SIZE=$SLURM_GPUS` without us having to edit code when we scale up.
TENSOR_PARALLEL_SIZE = int(os.environ.get("TP_SIZE", "1"))

PROMPT_PATHS = {
    "smoke_test": "prompts/smoke_test.txt",
    "math_test": "prompts/math_test.txt",
}
