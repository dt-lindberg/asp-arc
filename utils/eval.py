"""Evaluation: verify ASP programs against ARC training examples.

The harness injects input(Row, Col, Color) and color(0..9) facts for each
training example, runs Clingo, extracts output(Row, Col, Color) atoms from
the answer set, and compares to the expected output grid.
"""

from utils.grids import grid_to_input_facts, answer_set_to_grid, grid_diff
from utils.logger import get_logger

logger = get_logger(__name__)


def _check_syntax(program, run_clingo):
    """Run Clingo on the bare program to detect parse/ground errors before evaluation.

    Returns an error string if syntax errors are found, or None if the program parses.
    """
    status, result = run_clingo(program)
    if status is RuntimeError:
        if result:
            errors = "\n".join(str(x[1]).strip() for x in result)
        else:
            errors = "Unknown parse error"
        return errors
    return None


def verify_on_training_examples(program, train_examples, run_clingo):
    """Verify an ASP program against all training input-output examples.

    If the bare program fails Clingo's parser, evaluation is skipped entirely
    and all examples are reported as clingo_error with the shared error message.

    Args:
        program: ASP program string (no input and color facts).
        train_examples: list of {"input": grid, "output": grid} dicts.
        run_clingo: callable (program_str) -> (err, answer_sets | messages).

    Returns:
        list of result dicts, one per training example.
    """
    # Syntax check on the bare program, avoids running Clingo N times for the
    # same parse error, and gives clean error messages without line-number offsets
    # introduced by the injected input facts.
    syntax_error = _check_syntax(program, run_clingo)
    if syntax_error is not None:
        return [
            {
                "example_idx": i,
                "status": "clingo_error",
                "n_answer_sets": 0,
                "clingo_errors": syntax_error,
                "correct": False,
                "diff": None,
                "accuracy": 0.0,
                "grid_predicted": None,
                "grid_expected": ex["output"],
            }
            for i, ex in enumerate(train_examples)
        ]

    results = []

    for i, ex in enumerate(train_examples):
        expected = ex["output"]
        n_rows = len(expected)
        n_cols = len(expected[0])

        input_facts = grid_to_input_facts(ex["input"])
        full_program = input_facts + "\n\n" + program

        status, answer_sets_or_errors = run_clingo(full_program)

        result = {"example_idx": i}

        if status is RuntimeError:
            errors = "\n".join(str(x[1]).strip() for x in answer_sets_or_errors)
            result.update(
                status="clingo_error",
                n_answer_sets=0,
                clingo_errors=errors,
                correct=False,
                diff=None,
                accuracy=0.0,
                grid_predicted=None,
                grid_expected=expected,
            )
            logger.debug(f"  Example {i}: Clingo error — {errors[:120]}")

        elif len(answer_sets_or_errors) == 0:
            result.update(
                status="unsatisfiable",
                n_answer_sets=0,
                clingo_errors="0 answer sets (unsatisfiable)",
                correct=False,
                diff=None,
                accuracy=0.0,
                grid_predicted=None,
                grid_expected=expected,
            )
            logger.debug(f"  Example {i}: unsatisfiable")

        elif len(answer_sets_or_errors) != 1:
            n = len(answer_sets_or_errors)
            result.update(
                status="underconstrained",
                n_answer_sets=n,
                clingo_errors=f"{n} answer sets found (need exactly 1)",
                correct=False,
                diff=None,
                accuracy=0.0,
                grid_predicted=None,
                grid_expected=expected,
            )
            logger.debug(f"  Example {i}: {n} answer sets (underconstrained)")

        else:
            atoms = answer_sets_or_errors[0]
            predicted = answer_set_to_grid(atoms, n_rows, n_cols)
            diff_str, accuracy = grid_diff(predicted, expected)
            correct = accuracy == 1.0

            result.update(
                status="correct" if correct else "wrong_values",
                n_answer_sets=1,
                clingo_errors="",
                correct=correct,
                diff=diff_str,
                accuracy=round(accuracy, 4),
                grid_predicted=predicted,
                grid_expected=expected,
            )
            logger.debug(
                f"  Example {i}: {'CORRECT' if correct else f'WRONG (acc={accuracy:.2f})'}"
            )

        results.append(result)

    return results


def build_train_feedback(train_results):
    """Build a human-readable feedback string from training verification results.

    Used as <FEEDBACK> in the reattempt prompt.
    """
    parts = []
    for res in train_results:
        i = res["example_idx"]
        status = res["status"]

        if res["correct"]:
            parts.append(f"Example #{i + 1}: CORRECT (exact match)")
            continue

        lines = [f"Example #{i + 1}: INCORRECT"]

        if status == "clingo_error":
            lines.append(f"  Clingo error:\n{res['clingo_errors']}")

        elif status == "unsatisfiable":
            lines.append("  Program is UNSATISFIABLE — 0 answer sets produced.")

        elif status == "underconstrained":
            n = res["n_answer_sets"]
            lines.append(
                f"  Program is UNDERCONSTRAINED — {n} answer sets produced (need exactly 1)."
            )

        elif status in ("wrong_values", "shape_mismatch"):
            diff = res.get("diff", "(no diff available)")
            acc = res.get("accuracy", 0.0)
            lines.append(
                "  One answer set found, but it does not match the expected output.\n"
                "  Diff (prediction/expected for wrong cells, correct cells shown as-is):\n"
                + diff
            )
            lines.append(f"  Accuracy: {acc:.2f}")

        parts.append("\n".join(lines))

    return "\n\n".join(parts)


def all_correct(train_results):
    """Return True if every training example was solved correctly."""
    return all(r["correct"] for r in train_results)
