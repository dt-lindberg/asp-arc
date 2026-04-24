"""
General configuration file.
"""

# Seed to control randomness, acts on everything (incl. LLM)
SEED = 132

# CLINGO
CLINGO_MAX_MODELS = 1001
CLINGO_TIMEOUT = 30.0

# REFINEMENT LOOP
# MAX_ATTEMPTS: number of reattempt iterations after the initial step-0 program
# SEVERELY_UNDERCONSTRAINED_THRESHOLD: programs with more answer sets than this
#   get a simplified feedback message instead of showing variable atoms
# MAX_VARIABLE_ATOMS: limit variable atoms shown in feedback
MAX_ATTEMPTS = 6
SEVERELY_UNDERCONSTRAINED_THRESHOLD = 1000
MAX_VARIABLE_ATOMS = 30

# Logging
# LOG_LEVEL is one of error/warning/info/debug
LOG_LEVEL = "debug"
LOG_FORMAT = (
    "%(asctime)s [%(levelname)s] %(name)s - %(funcName)s:%(lineno)d > %(message)s"
)
ALLOWED_LOGGERS = ("__main__", "utils", "agent")

# Paths to ARC datasets
ARC_V1_TRAINING = "arc-puzzles/ARC-AGI/data/training"
ARC_V1_EVALUATION = "arc-puzzles/ARC-AGI/data/evaluation"
ARC_V2_TRAINING = "arc-puzzles/ARC-AGI-2/data/training"
ARC_V2_EVALUATION = "arc-puzzles/ARC-AGI-2/data/evaluation"
