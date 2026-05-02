"""General configuration: shared by all components."""

# Seed to control randomness (incl. vLLM sampling)
SEED = 132

# Logging — one of error/warning/info/debug
LOG_LEVEL = "debug"
LOG_FORMAT = (
    "%(asctime)s [%(levelname)s] %(name)s - %(funcName)s:%(lineno)d > %(message)s"
)
ALLOWED_LOGGERS = ("__main__", "llm", "utils")
