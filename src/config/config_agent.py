# Path to model weights

DEFAULT_ENGINE = "nemotron-cascade-2"
MODEL_REPO_ID = "chankhavu/Nemotron-Cascade-2-30B-A3B-NVFP4"  # HuggingFace repo id
THINKING = True

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
MAX_TOKENS = 200_000
MAX_MODEL_LEN = 262_144
MAX_NUM_BATCHED_TOKENS = 8192
MAX_NUM_SEQS = 40
GPU_MEMORY_UTILIZATION = 0.95
TEMPERATURE = 1.0
TOP_P = 0.95
TOP_K = 20
MIN_P = 0.0
PRESENCE_PENALTY = 0.0
REPETITION_PENALTY = 1.0


PROMPT_PATHS = {
    "initial": "prompts/initial_prompt.txt",
    "reattempt": "prompts/reattempt.txt",
}
