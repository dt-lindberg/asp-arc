"""Sequential puzzle sampler across all NVARC outputs/ parquets.

Walks parquets in alphabetical order and yields unique (puzzle_name1, puzzle_name2)
pairs in first-appearance order. For each pair, one SID is sampled uniformly from
the rows for that pair. Pairs whose grid files are absent (nvarc_full/ split) are
skipped. A cross-parquet dedup set prevents yielding the same pair twice.
"""

import glob
import os

import pandas as pd

from config.config_nvarc import NVARC_GRIDS_DIR, NVARC_OUTPUTS_DIR
from utils.logger import get_logger
from utils.nvarc_data import _COLUMNS

logger = get_logger(__name__)


def sample_puzzles(n: int):
    """Yield up to n puzzle rows with guaranteed existing grid files.

    Yields: (pd.Series row, source_parquet_filename: str)
    """
    files = sorted(glob.glob(f"{NVARC_OUTPUTS_DIR}/data-*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet files in {NVARC_OUTPUTS_DIR}")

    seen = set()
    n_yielded = 0

    for file_path in files:
        if n_yielded >= n:
            break

        df = pd.read_parquet(file_path, columns=_COLUMNS)

        for (p1, p2), group in df.groupby(["puzzle_name1", "puzzle_name2"], sort=False):
            if n_yielded >= n:
                break

            key = (p1, p2)
            if key in seen:
                continue

            grid_path = os.path.join(NVARC_GRIDS_DIR, str(p1), f"{p1}_{p2}.json")
            if not os.path.isfile(grid_path):
                continue

            seen.add(key)

            row = group.sample(n=1).iloc[0]

            n_yielded += 1
            yield row, os.path.basename(file_path)

    if n_yielded < n:
        logger.warning(f"Only yielded {n_yielded}/{n} puzzles — exhausted all parquets")

    logger.debug(f"Sampled {n_yielded} unique puzzles (requested {n})")
