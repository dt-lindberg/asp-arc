"""Read-only access to NVARC data on scratch. No formatting or extraction logic."""

import glob
import json
import os

import pandas as pd

from config.config_nvarc import NVARC_GRIDS_DIR, NVARC_OUTPUTS_DIR
from utils.logger import get_logger

logger = get_logger(__name__)

_COLUMNS = [
    "mix_name",
    "puzzle_name1",
    "puzzle_name2",
    "model_name",
    "reasoning_level",
    "prompt",
    "completion",
    "sid",
]


def load_output_row(row_index: int = 0) -> pd.Series:
    """Return one row from the outputs/ parquets (sorted by filename)."""
    files = sorted(glob.glob(f"{NVARC_OUTPUTS_DIR}/data-*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet files found in {NVARC_OUTPUTS_DIR}")
    df = pd.read_parquet(files[0], columns=_COLUMNS)
    logger.debug(f"Loaded {len(df)} rows from {files[0]}, picking index {row_index}")
    return df.iloc[row_index]


def load_grid_pairs(puzzle_name1: str, puzzle_name2: str) -> list[tuple]:
    """Return all (input_grid, output_grid) pairs for the given puzzle instance.

    puzzle_name1 is the folder, puzzle_name2 is the file id:
      grids/<puzzle_name1>/<puzzle_name1>_<puzzle_name2>.json
    Each JSON file contains ~30 pairs.
    """
    path = os.path.join(
        NVARC_GRIDS_DIR, puzzle_name1, f"{puzzle_name1}_{puzzle_name2}.json"
    )
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Grid file not found: {path}")
    with open(path) as f:
        data = json.load(f)
    pairs = [(item["input"], item["output"]) for item in data]
    logger.debug(f"Loaded {len(pairs)} grid pairs from {path}")
    return pairs
