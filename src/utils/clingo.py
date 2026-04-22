"""
Clingo runner; grounding and solving logic with timeouts
"""

import threading

from config.config import CLINGO_MAX_MODELS, CLINGO_TIMEOUT
from utils.logger import get_logger

logger = get_logger(__name__)


# Clingo Python context, ARC programs don't call out to Python, but Control.ground
# requires a context object, so we pass this empty stub.
class _Context:
    pass


def run_clingo(program):
    """
    Ground and solve an ASP program with Clingo, enforcing a timeout.

    * Returns (None, answer_sets) on success, where answer_sets is a list of
      lists-of-atom-strings (one inner list per model).
    * Returns (RuntimeError, clingo_messages) on parse/ground error or timeout,
      where clingo_messages is a list of (code, message) tuples emitted by the
      Clingo logger (may be empty for unexpected failures).
    """
    from clingo.control import Control

    clingo_messages = []

    def _clingo_logger(code, message):
        clingo_messages.append((code, message))

    ctl = Control(
        [str(CLINGO_MAX_MODELS), "--warn=none", "--opt-mode=optN", "-t", "4"],
        logger=_clingo_logger,
    )
    models = []

    try:
        # Strip non-ASCII chars that occasionally appear in LLM output and break Clingo
        program_clean = program.encode("ascii", errors="replace").decode("ascii")
        logger.debug(f"Adding program to Clingo ({len(program_clean)} chars)")
        ctl.add("base", [], program_clean)
    except RuntimeError as e:
        logger.debug(f"Clingo parse error: {e} ({len(clingo_messages)} messages)")
        return RuntimeError, clingo_messages
    except Exception as e:
        logger.error(f"Clingo add() failed: {e}")
        return RuntimeError, []

    # Ground in a daemon thread so we can enforce a hard timeout
    ground_exc = [None]
    ground_done = threading.Event()

    def _do_ground():
        try:
            ctl.ground([("base", [])], context=_Context())
        except Exception as e:
            ground_exc[0] = e
        finally:
            ground_done.set()

    threading.Thread(target=_do_ground, daemon=True).start()
    if not ground_done.wait(CLINGO_TIMEOUT):
        logger.warning(f"Clingo ground() timed out after {CLINGO_TIMEOUT}s")
        return RuntimeError, []

    if ground_exc[0] is not None:
        e = ground_exc[0]
        if isinstance(e, RuntimeError):
            logger.debug(f"Clingo grounding error: {e}")
            return RuntimeError, clingo_messages
        logger.error(f"Clingo grounding failed: {e}")
        return RuntimeError, []

    on_model = lambda model: models.append(model.symbols(atoms=True))  # noqa: E731 (hiding Ruff formatting error)

    with ctl.solve(on_model=on_model, async_=True) as handle:
        finished = handle.wait(CLINGO_TIMEOUT)
        if not finished:
            handle.cancel()
            handle.wait()
            logger.debug(f"Clingo solve() timed out ({len(models)} models so far)")

    models = [[str(atom) for atom in m] for m in models]
    logger.debug(f"Clingo: {len(models)} answer set(s)")
    return None, models
