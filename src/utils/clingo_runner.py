"""Clingo runner: dispatches grounding+solving to the process pool.

The actual work happens in clingo_pool._do_ground_and_solve, running in a
child process so timed-out jobs can be force-killed. This module exists to
preserve the (err, payload) return contract that asp_validator depends on.
"""

from config.config import CLINGO_TIMEOUT
from utils.clingo_pool import get_pool


def run_clingo(program: str):
    """Ground and solve `program` with Clingo, enforcing a timeout.

    Returns (None, answer_sets) on success, where answer_sets is a list of
    lists-of-atom-strings (one inner list per model).

    Returns (RuntimeError, clingo_messages) on parse/ground error or timeout,
    where clingo_messages is a list of (code, message) tuples (may be empty).
    """
    err, payload = get_pool().run(program, timeout=CLINGO_TIMEOUT)
    if err:
        return RuntimeError, payload
    return None, payload
