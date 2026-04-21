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
MAX_TOKENS = 4_000
MAX_MODEL_LEN = 32_000
MAX_NUM_BATCHED_TOKENS = 2_048
MAX_NUM_SEQS = 10
TEMPERATURE = 0.7
GPU_MEMORY_UTILIZATION = 0.95
TOP_P = 0.8
TOP_K = 20
MIN_P = 0.01
PRESENCE_PENALTY = 0.0
REPETITION_PENALT = 1.0


# MAX_THINK_TOKENS = 4_000
# THINK_START_TOKEN_ID = 151667
# THINK_END_TOKEN_ID = 151668

PROMPT_PATHS = {
    "analysis": "prompts/1_analysis.txt",
    "predicates": "prompts/2_predicates.txt",
    "choice_rules": "prompts/3_choice_rules.txt",
    "constraints": "prompts/4_constraints.txt",
    "reattempt": "prompts/5_reattempt.txt",
}
