"""Random puzzle sampler across all NVARC outputs/ parquets.

Reads only parquet footer metadata to determine row counts, then lazily loads
one file at a time as it streams rows to the caller. Each file is loaded at
most once. Duplicate (puzzle_name1, puzzle_name2) pairs are skipped silently,
so the caller may receive slightly fewer than n rows — that is expected.
"""

import glob
import os
import random

import pandas as pd
import pyarrow.parquet as pq

from config.config_nvarc import NVARC_OUTPUTS_DIR
from utils.logger import get_logger
from utils.nvarc_data import _COLUMNS

logger = get_logger(__name__)


def sample_puzzles(n: int, seed: int = 42):
    """Yield up to n unique puzzle rows sampled randomly across all parquets.

    Yields: (pd.Series row, source_parquet_filename: str)
    """
    files = sorted(glob.glob(f"{NVARC_OUTPUTS_DIR}/data-*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet files in {NVARC_OUTPUTS_DIR}")

    # Row counts from parquet footer — no data pages read
    row_counts = [pq.read_metadata(f).num_rows for f in files]
    logger.debug(f"{len(files)} parquet files, {sum(row_counts):,} total rows")

    rng = random.Random(seed)
    n_candidates = int(n * 1.5) + 20

    # Weighted sample of (file_idx, row_idx) pairs
    file_indices = rng.choices(range(len(files)), weights=row_counts, k=n_candidates)
    candidates = sorted(
        [(fi, rng.randint(0, row_counts[fi] - 1)) for fi in file_indices],
        key=lambda x: x[0],   # sort by file so each parquet is loaded once
    )

    seen = set()
    n_yielded = 0
    current_file_idx = -1
    df = None

    for file_idx, row_idx in candidates:
        if n_yielded >= n:
            break

        if file_idx != current_file_idx:
            df = pd.read_parquet(files[file_idx], columns=_COLUMNS)
            current_file_idx = file_idx

        row = df.iloc[row_idx]
        key = (row.puzzle_name1, row.puzzle_name2)
        if key in seen:
            continue

        seen.add(key)
        n_yielded += 1
        yield row, os.path.basename(files[file_idx])

    logger.debug(f"Sampled {n_yielded} unique puzzles (requested {n})")
