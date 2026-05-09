"""Sequential puzzle sampler across all NVARC outputs/ parquets.

Walks parquets in alphabetical order and yields unique (puzzle_name1, puzzle_name2)
pairs in first-appearance order. For each pair, one SID is sampled uniformly from
the rows for that pair. Pairs whose grid files are absent (nvarc_full/ split) are
skipped. A cross-parquet dedup set prevents yielding the same pair twice.

Pairs listed in solved_puzzles.tsv at the repo root (produced by
scripts/find_solved_puzzles.py) are also skipped. Because skipping happens
inside the loop and only successful yields advance n_yielded, the caller
still receives the full requested count whenever the parquets contain
enough unsolved puzzles.
"""

import glob
import os

import pandas as pd

from config.config_nvarc import NVARC_GRIDS_DIR, NVARC_OUTPUTS_DIR
from utils.logger import get_logger
from utils.nvarc_data import _COLUMNS

logger = get_logger(__name__)

# Repo root = parent of src/, computed from this file's location so it
# works regardless of the caller's CWD.
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
SOLVED_PUZZLES_FILE = os.path.join(_REPO_ROOT, "solved_puzzles.tsv")


def _load_solved_puzzles() -> set[tuple[str, str]]:
    """Load already-solved (puzzle_name1, puzzle_name2) pairs from
    solved_puzzles.tsv. Returns an empty set if the file is absent."""
    if not os.path.isfile(SOLVED_PUZZLES_FILE):
        logger.info(
            f"No solved-puzzles file at {SOLVED_PUZZLES_FILE}; "
            "no puzzles will be skipped on that basis."
        )
        return set()
    solved = set()
    with open(SOLVED_PUZZLES_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            p1, p2 = line.split("\t")
            solved.add((p1, p2))
    logger.info(
        f"Loaded {len(solved)} already-solved puzzles from "
        f"{SOLVED_PUZZLES_FILE}; these will be skipped during sampling."
    )
    return solved


def sample_puzzles(n: int):
    """Yield up to n puzzle rows with guaranteed existing grid files.

    Yields: (pd.Series row, source_parquet_filename: str)
    """
    files = sorted(glob.glob(f"{NVARC_OUTPUTS_DIR}/data-*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet files in {NVARC_OUTPUTS_DIR}")

    solved = _load_solved_puzzles()
    seen = set()
    n_yielded = 0
    n_skipped_solved = 0

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

            if key in solved:
                seen.add(key)
                n_skipped_solved += 1
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

    logger.debug(
        f"Sampled {n_yielded} unique puzzles (requested {n}); "
        f"skipped {n_skipped_solved} already-solved"
    )
