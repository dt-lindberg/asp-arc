# MODEL
MODEL_PATH = (
    "/home/dlindberg/.cache/huggingface/hub/"
    "models--unsloth--Qwen3-30B-A3B-Instruct-2507-GGUF/snapshots/"
    "eea7b2be5805a5f151f8847ede8e5f9a9284bf77/"
    "Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf"
)

# Seed for vLLM — deterministic cache reuse across runs
SEED = 132

# Enable/disable thinking mode
THINKING = True

# INFERENCE / SAMPLING
MAX_TOKENS = 6000          # = MAX_THINK_TOKENS + 2000 response budget
MAX_THINK_TOKENS = 4000    # thinking budget before ThinkLogitsProcessor forces </think>
MAX_MODEL_LEN = 32768      # max input+output tokens per sequence
MAX_NUM_BATCHED_TOKENS = 2048
MAX_NUM_SEQS = 10          # max batch size
TEMPERATURE = 0.7
GPU_MEMORY_UTILIZATION = 0.95
THINK_START_TOKEN_ID = 151667  # <think>  — from find_think_tokens.job
THINK_END_TOKEN_ID = 151668    # </think> — from find_think_tokens.job
TOP_P = 0.8
TOP_K = 20
MIN_P = 0.01

# CLINGO
CLINGO_MAX_MODELS = 1001
CLINGO_TIMEOUT = 30.0

# REFINEMENT LOOP
# MAX_ATTEMPTS: number of reattempt iterations after the initial step-0 program
# SEVERELY_UNDERCONSTRAINED_THRESHOLD: programs with more answer sets than this
#   get a simplified feedback message instead of showing variable atoms
# MAX_VARIABLE_ATOMS: limit variable atoms shown in feedback
MAX_ATTEMPTS = 5
SEVERELY_UNDERCONSTRAINED_THRESHOLD = 1000
MAX_VARIABLE_ATOMS = 30

# PIPELINE
DEFAULT_ENGINE = "qwen3-30b-local"

PROMPT_PATHS = {
    "analysis":      "prompts/1_analysis.txt",
    "predicates":    "prompts/2_predicates.txt",
    "choice_rules":  "prompts/3_choice_rules.txt",
    "constraints":   "prompts/4_constraints.txt",
    "reattempt":     "prompts/5_reattempt.txt",
}

# ARC DATASET
ARC_V1_TRAINING = "arc-puzzles/ARC-AGI/data/training"
ARC_V1_EVALUATION = "arc-puzzles/ARC-AGI/data/evaluation"
ARC_V2_TRAINING = "arc-puzzles/ARC-AGI-2/data/training"
ARC_V2_EVALUATION = "arc-puzzles/ARC-AGI-2/data/evaluation"

# LOGGING
LOG_FORMAT = (
    "%(asctime)s [%(levelname)s] %(name)s - %(funcName)s:%(lineno)d > %(message)s"
)
ALLOWED_LOGGERS = ("__main__", "pipeline", "arc_loader", "eval", "vllm_engine")
