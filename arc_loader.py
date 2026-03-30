"""Load and sample ARC-AGI puzzles from the local dataset directories."""

import json
import os
import random

from config import ARC_V1_TRAINING, ARC_V1_EVALUATION, ARC_V2_TRAINING, ARC_V2_EVALUATION
from logger import get_logger

logger = get_logger(__name__)

DATASET_PATHS = {
    "arc-v1-training": ARC_V1_TRAINING,
    "arc-v1-evaluation": ARC_V1_EVALUATION,
    "arc-v2-training": ARC_V2_TRAINING,
    "arc-v2-evaluation": ARC_V2_EVALUATION,
}


def list_puzzles(dataset="arc-v1-training"):
    """Return a sorted list of puzzle IDs in the given dataset."""
    path = DATASET_PATHS[dataset]
    ids = sorted(f[:-5] for f in os.listdir(path) if f.endswith(".json"))
    logger.debug(f"Found {len(ids)} puzzles in {dataset}")
    return ids


def load_puzzle(puzzle_id, dataset="arc-v1-training"):
    """Load a single ARC puzzle by ID.

    Returns:
        dict with keys: id, dataset, train (list of {input, output}), test (list of {input}).
    """
    path = os.path.join(DATASET_PATHS[dataset], f"{puzzle_id}.json")
    with open(path) as f:
        data = json.load(f)
    puzzle = {"id": puzzle_id, "dataset": dataset, **data}
    logger.debug(
        f"Loaded {puzzle_id}: {len(puzzle['train'])} train, {len(puzzle['test'])} test"
    )
    return puzzle


def get_puzzles(dataset="arc-v1-training", n=3, seed=42):
    """Load N randomly sampled puzzles from a dataset.

    Args:
        dataset: one of the keys in DATASET_PATHS.
        n: number of puzzles to load.
        seed: random seed for reproducible sampling.

    Returns:
        list of puzzle dicts.
    """
    ids = list_puzzles(dataset)
    rng = random.Random(seed)
    selected = rng.sample(ids, min(n, len(ids)))
    logger.info(f"Selected puzzles: {selected}")
    return [load_puzzle(pid, dataset) for pid in selected]


def get_puzzles_by_ids(puzzle_ids, dataset="arc-v1-training"):
    """Load specific puzzles by their IDs."""
    return [load_puzzle(pid, dataset) for pid in puzzle_ids]
