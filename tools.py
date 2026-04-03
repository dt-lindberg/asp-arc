"""Tool implementations for the agentic syntax-fix loop.

run_clingo: runs an ASP snippet through Clingo and returns human-readable output.
edit_code:  patches an in-memory ASP program string (file_path is ignored).
"""

import threading

from logger import get_logger

logger = get_logger("tools")


def run_clingo(code, num_models=1, extra_args=""):
    """Run ASP code through Clingo and return a human-readable result string.

    Uses the clingo Python API (no separate CLI binary required).

    Args:
        code: ASP source string
        num_models: max answer sets to find (0 = all, default 1)
        extra_args: ignored (kept for API compatibility with docs template)

    Returns:
        Human-readable string: parse/ground errors, UNSATISFIABLE, or answer sets.
    """
    from clingo.control import Control

    clingo_messages = []

    def _clingo_logger(code, message):
        clingo_messages.append(str(message).strip())

    ctl = Control([str(num_models), "--warn=none"], logger=_clingo_logger)
    models = []

    try:
        program_clean = code.encode("ascii", errors="replace").decode("ascii")
        ctl.add("base", [], program_clean)
    except RuntimeError as e:
        errors = "\n".join(clingo_messages) or str(e)
        return f"PARSE ERROR:\n{errors}"
    except Exception as e:
        return f"ERROR: {e}"

    ground_exc = [None]
    ground_done = threading.Event()

    def _do_ground():
        try:
            ctl.ground([("base", [])])
        except Exception as e:
            ground_exc[0] = e
        finally:
            ground_done.set()

    threading.Thread(target=_do_ground, daemon=True).start()
    if not ground_done.wait(30):
        return "ERROR: Clingo grounding timed out after 30s."

    if ground_exc[0] is not None:
        errors = "\n".join(clingo_messages) or str(ground_exc[0])
        return f"GROUND ERROR:\n{errors}"

    on_model = lambda model: models.append([str(a) for a in model.symbols(atoms=True)])
    with ctl.solve(on_model=on_model, async_=True) as handle:
        finished = handle.wait(30)
        if not finished:
            handle.cancel()
            handle.wait()
            return f"TIMEOUT: solve timed out ({len(models)} model(s) found so far)."

    if not models:
        return "UNSATISFIABLE (0 answer sets)"

    lines = []
    for i, m in enumerate(models[: max(num_models, 1)], 1):
        lines.append(f"Answer: {i}")
        lines.append(" ".join(sorted(m)) if m else "(empty model)")
    lines.append(f"SATISFIABLE ({len(models)} answer set(s))")
    return "\n".join(lines)


def edit_code(
    current_program, file_path=None, content=None, old_str=None, new_str=None
):
    """Patch the in-memory ASP program string.

    Args:
        current_program: current ASP program string
        file_path: ignored
        content: if set, replaces the entire program with this content
        old_str: exact string to replace (must exist verbatim in current_program)
        new_str: replacement text (use "" or omit to delete old_str)

    Returns:
        (new_program, result_message)
    """
    if content is not None:
        msg = (
            "Error: full rewrites via 'content' are disabled — they destroy the original program logic. "
            "Use old_str + new_str to make a targeted patch instead.\n\n"
            f"Current program:\n```asp\n{current_program}\n```"
        )
        return current_program, msg

    if old_str is not None:
        if old_str not in current_program:
            msg = (
                "Error: old_str not found in the current program. "
                "Check for exact whitespace and newlines.\n\n"
                f"Current program:\n```asp\n{current_program}\n```"
            )
            return current_program, msg
        replacement = new_str if new_str is not None else ""
        new_program = current_program.replace(old_str, replacement, 1)
        msg = (
            f"Patched: replaced {len(old_str)} chars with {len(replacement)} chars.\n\n"
            f"Current program:\n```asp\n{new_program}\n```"
        )
        return new_program, msg

    msg = (
        "Error: provide either 'content' (full rewrite) or 'old_str'+'new_str' (targeted patch).\n\n"
        f"Current program:\n```asp\n{current_program}\n```"
    )
    return current_program, msg
