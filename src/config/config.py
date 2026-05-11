"""General configuration: shared by all components."""

import os

# Seed to control randomness (incl. vLLM sampling)
SEED = 132

# Logging — one of error/warning/info/debug
LOG_LEVEL = "debug"
LOG_FORMAT = (
    "%(asctime)s [%(levelname)s] %(name)s - %(funcName)s:%(lineno)d > %(message)s"
)
ALLOWED_LOGGERS = ("__main__", "llm", "utils")

# Clingo
CLINGO_TIMEOUT = 5  # seconds per ground/solve phase
CLINGO_MAX_MODELS = 2  # stop after 2 — enough to detect ambiguity

# Clingo grounding+solving runs in a pool of long-lived child processes so
# that timed-out jobs can be force-killed (clingo.Control.ground() has no
# cancellation API and Python threads can't be killed). Workers are also
# recycled preventively after MAX_JOBS jobs to bound allocator drift.
CLINGO_POOL_WORKERS = int(os.environ.get("CLINGO_POOL_WORKERS", "4"))
CLINGO_WORKER_MAX_JOBS = int(os.environ.get("CLINGO_WORKER_MAX_JOBS", "200"))

# Refinement loop
MAX_REFINEMENT_ATTEMPTS = 2  # max rounds of fix-and-retry per candidate

# Bounded worker pool for batch_pipeline. At any moment, at most this many
# puzzles are loaded in RAM and (via their independent gen→validate→refine
# coroutines) feeding requests into vLLM's queue. Set high enough that
# MAX_NUM_SEQS workers are always saturated even when some are mid-validation;
# a few × MAX_NUM_SEQS is a good rule of thumb.
MAX_CONCURRENT_PUZZLES = int(os.environ.get("MAX_CONCURRENT_PUZZLES", "100"))
