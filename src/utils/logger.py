"""Centralized low-code logger."""

import logging
import os
import sys
import time

from config.config import LOG_FORMAT, ALLOWED_LOGGERS

_LOG_DIR = os.getenv("LOG_DIR", "logs")


def _set_log_level(log_level):
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }
    return level_map.get(log_level.strip().lower(), logging.DEBUG)


def setup_logging(log_level="info", force=False):
    """Configure root logging with stdout + log file under logs/<date>/."""
    log_dir = os.path.join(_LOG_DIR, time.strftime("%Y%m%d"))
    log_file = os.path.join(log_dir, f"log_{time.strftime('%Y%m%d_%H%M%S')}.log")
    os.makedirs(log_dir, exist_ok=True)

    level = _set_log_level(log_level)
    handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(log_file)]
    logging.basicConfig(level=level, format=LOG_FORMAT, handlers=handlers, force=force)

    logging.getLogger().setLevel(logging.WARNING)
    for name in ALLOWED_LOGGERS:
        logging.getLogger(name).setLevel(level)

    logging.captureWarnings(True)


def get_logger(name):
    return logging.getLogger(name)
