# MODEL
MODEL_PATH = "chankhavu/Nemotron-Cascade-2-30B-A3B-NVFP4"

# Seed for vLLM — deterministic cache reuse across runs
SEED = 132

# INFERENCE / SAMPLING
MAX_MODEL_LEN = 220_000     # total context window; the only token limit
TEMPERATURE = 1.0
TOP_P = 0.95
TOP_K = 20

# CLINGO
CLINGO_MAX_MODELS = 1001
CLINGO_TIMEOUT = 30.0

# REFINEMENT LOOP
MAX_ATTEMPTS = 5
SEVERELY_UNDERCONSTRAINED_THRESHOLD = 1000
MAX_VARIABLE_ATOMS = 30

# PIPELINE
DEFAULT_ENGINE = "nemotron-cascade-2"

PROMPT_PATHS = {
    "single_step": "prompts/single_step.txt",
    "reattempt":   "prompts/5_reattempt.txt",
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
ALLOWED_LOGGERS = ("__main__", "pipeline", "arc_loader", "eval", "nemotron_engine")
