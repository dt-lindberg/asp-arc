"""Evaluation: verify ASP programs against ARC training examples.

The harness prepends input(Row, Col, Color) facts for each specific training
example, runs Clingo, extracts output(Row, Col, Color) atoms from the answer
set, and compares to the expected output grid.
"""

from utils import grid_to_input_facts, answer_set_to_grid, grid_diff
from logger import get_logger

logger = get_logger(__name__)


def verify_on_training_examples(program, train_examples, pipeline):
    """Verify an ASP program against all training input-output examples.

    The program must use input(Row, Col, Color) as an input predicate (the
    harness injects those facts) and produce output(Row, Col, Color) atoms.

    Args:
        program: ASP program string (without input facts).
        train_examples: list of {"input": grid, "output": grid} dicts.
        pipeline: Pipeline instance with gen_answer_set().

    Returns:
        list of result dicts, one per training example.
    """
    results = []

    for i, ex in enumerate(train_examples):
        expected = ex["output"]
        n_rows = len(expected)
        n_cols = len(expected[0]) if expected else 0

        input_facts = grid_to_input_facts(ex["input"])
        full_program = input_facts + "\n\n" + program

        status, answer_sets_or_errors = pipeline.gen_answer_set(full_program)

        result = {"example_idx": i}

        if status is RuntimeError:
            errors = "\n".join(str(x[1]) for x in answer_sets_or_errors)
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
                clingo_errors=f"{n} answer sets found (expected exactly 1)",
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
