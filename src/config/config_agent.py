# Path to model weights

DEFAULT_ENGINE = "qwen36-35B"
MODEL_REPO_ID = "Qwen/Qwen3.6-35B-A3B-FP8"  # HuggingFace repo id
THINKING = True
LANGUAGE_MODEL_ONLY = True

# ┌─────────────────────────────┬──────────────────────────────────────────────────┐
# │ Parameter                   │ Description                                      │
# ├─────────────────────────────┼──────────────────────────────────────────────────┤
# │ MAX_TOKENS                  │ max output tokens/seq = think budget + response  │
# │ MAX_MODEL_LEN               │ max input+output tokens per sequence (KV slot)   │
# │ MAX_NUM_BATCHED_TOKENS      │ max input tokens across batch per scheduler step │
# │ MAX_NUM_SEQS                │ max sequences in flight (batch size)             │
# │ TEMPERATURE                 │ sampling temperature; 0 = greedy                 │
# │ GPU_MEMORY_UTILIZATION      │ fraction of VRAM reserved for KV-cache           │
# │ TOP_P                       │ nucleus sampling probability mass cutoff         │
# │ TOP_K                       │ vocab truncation to top-k before sampling        │
# │ MIN_P                       │ min token probability relative to top token      │
# └─────────────────────────────┴──────────────────────────────────────────────────┘
MAX_TOKENS = 81_920
MAX_MODEL_LEN = 94_000
MAX_NUM_BATCHED_TOKENS = 8192
MAX_NUM_SEQS = 10
GPU_MEMORY_UTILIZATION = 0.95
TEMPERATURE = 0.8
TOP_P = 0.95
TOP_K = 20
MIN_P = 0.0
PRESENCE_PENALTY = 0.0
REPETITION_PENALTY = 1.0


PROMPT_PATHS = {
    "initial": "prompts/initial_prompt.txt",
    "reattempt": "prompts/reattempt.txt",
}
