"""Validate a generated ASP program against NVARC input-output grid pairs."""

import re

from utils.clingo_runner import run_clingo
from utils.nvarc_data import load_grid_pairs
from utils.nvarc_formatting import grid_to_input_facts
from utils.logger import get_logger

logger = get_logger(__name__)


def _check_answer_set(answer_set: list[str], expected_grid: list) -> bool:
    expected = {
        (r, c): v
        for r, row in enumerate(expected_grid)
        for c, v in enumerate(row)
    }
    actual = {}
    for atom in answer_set:
        m = re.match(r"^output\((\d+),(\d+),(\d+)\)$", atom)
        if m:
            actual[(int(m.group(1)), int(m.group(2)))] = int(m.group(3))
    return actual == expected


def validate_asp_program(asp_code: str, puzzle_name1: str, puzzle_name2: str) -> dict:
    """Validate asp_code against all grid pairs for the given puzzle instance.

    Returns a dict:
      total   - number of pairs tested
      correct - number of pairs that passed
      passed  - True iff all pairs passed
      pairs   - list of per-pair result dicts with keys: index, status,
                and optionally: error_messages, actual_atoms
                status is one of: correct | error | wrong_count(N) | wrong_output
    """
    pairs = load_grid_pairs(puzzle_name1, puzzle_name2)
    results = []
    clingo_messages = []

    for i, (input_grid, expected_output) in enumerate(pairs):
        facts = grid_to_input_facts(input_grid)
        program = facts + "\n\n" + asp_code
        err, models = run_clingo(program)

        pair_result = {"index": i}

        if err is not None:
            pair_result["status"] = "error"
            pair_result["error_messages"] = [msg for _, msg in models]
            clingo_messages.extend(msg for _, msg in models)
        elif len(models) == 0:
            pair_result["status"] = "wrong_count(0)"
        elif len(models) > 1:
            pair_result["status"] = f"wrong_count({len(models)})"
            pair_result["actual_atoms"] = models[:2]
        elif not _check_answer_set(models[0], expected_output):
            pair_result["status"] = "wrong_output"
            pair_result["actual_atoms"] = models[0]
        else:
            pair_result["status"] = "correct"

        results.append(pair_result)
        logger.debug(f"Pair {i:3d}: {pair_result['status']}")

    n_correct = sum(1 for r in results if r["status"] == "correct")
    # Deduplicate by normalising away line/col numbers, which shift per pair
    # because each pair prepends a different number of input() facts.
    seen = set()
    unique_errors = []
    for m in clingo_messages:
        key = re.sub(r"<block>:\d+:\d+(-\d+)?(:\d+)?", "<block>:?", m)
        if key not in seen:
            seen.add(key)
            unique_errors.append(m)
    return {
        "total":        len(pairs),
        "correct":      n_correct,
        "passed":       len(pairs) > 0 and n_correct == len(pairs),
        "pairs":        results,
        "clingo_errors": unique_errors,
    }
