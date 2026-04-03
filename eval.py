"""Evaluation: verify ASP programs against ARC training examples.

The harness prepends input(Row, Col, Color) facts for each specific training
example, runs Clingo, extracts output(Row, Col, Color) atoms from the answer
set, and compares to the expected output grid.
"""

from utils import grid_to_input_facts, answer_set_to_grid, grid_diff
from logger import get_logger

logger = get_logger(__name__)


def _check_syntax(program, pipeline):
    """Run Clingo on the bare program to detect parse/ground errors before evaluation.

    Returns an error string if syntax errors are found, or None if the program parses
    cleanly (regardless of satisfiability).
    """
    status, result = pipeline.gen_answer_set(program)
    if status is RuntimeError:
        if result:
            errors = "\n".join(str(x[1]).strip() for x in result)
        else:
            errors = "Unknown parse error"
        return errors
    return None


def verify_on_training_examples(program, train_examples, pipeline):
    """Verify an ASP program against all training input-output examples.

    The program must use input(Row, Col, Color) as an input predicate (the
    harness injects those facts) and produce output(Row, Col, Color) atoms.

    If the bare program fails Clingo's parser, evaluation is skipped entirely
    and all examples are reported as clingo_error with the shared error message.

    Args:
        program: ASP program string (without input facts).
        train_examples: list of {"input": grid, "output": grid} dicts.
        pipeline: Pipeline instance with gen_answer_set().

    Returns:
        list of result dicts, one per training example.
    """
    # Syntax check on the bare program — avoids running Clingo N times for the
    # same parse error, and gives clean error messages without line-number offsets
    # introduced by the injected input facts.
    syntax_error = _check_syntax(program, pipeline)
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
        n_cols = len(expected[0]) if expected else 0

        input_facts = grid_to_input_facts(ex["input"])
        full_program = input_facts + "\n\n" + program

        status, answer_sets_or_errors = pipeline.gen_answer_set(full_program)

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
            # Extract first answer set to give the model a concrete example to work with
            first_atoms = answer_sets_or_errors[0]
            first_predicted = answer_set_to_grid(first_atoms, n_rows, n_cols)
            diff_str, accuracy = grid_diff(first_predicted, expected)
            result.update(
                status="underconstrained",
                n_answer_sets=n,
                clingo_errors=f"{n} answer sets found (expected exactly 1)",
                correct=False,
                diff=diff_str,
                accuracy=round(accuracy, 4),
                grid_predicted=first_predicted,
                grid_expected=expected,
                answer_set=first_atoms,
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
                answer_set=atoms,
            )
            logger.debug(
                f"  Example {i}: {'CORRECT' if correct else f'WRONG (acc={accuracy:.2f})'}"
            )

        results.append(result)

    return results


def _annotate_clingo_error(error_str):
    """Append human-readable hints to cryptic Clingo error messages."""
    hints = []
    low = error_str.lower()
    if "unexpected =" in low and any(
        a in low for a in ("#count", "#sum", "#min", "#max")
    ):
        hints.append(
            "Hint: Aggregate on the right-hand side: `Var = #aggr{...}`, not `#aggr{...} = Var`"
        )
    if "aggregate(" in low:
        hints.append(
            "Hint: `aggregate(...)` is SWI-Prolog — use `N = #count { X : pred(X) }` instead"
        )
    if "#mod" in low:
        hints.append("Hint: Use `\\` for modulo (not `#mod`). Example: `C \\ 2 = 0`")
    if "exactly" in low and "unexpected" in low:
        hints.append("Hint: Use `1 { ... } 1` instead of `{ ... } exactly 1`")
    if "unsafe variable" in low:
        hints.append(
            "Hint: Every variable must appear in at least one positive literal. "
            "Add a positive binding before using the variable under `not`."
        )
    if "unexpected <variable>" in low and "#const" in low:
        hints.append("Hint: `#const` names must be lowercase identifiers")
    return error_str + ("\n" + "\n".join(hints) if hints else "")


def build_train_feedback(train_results):
    """Build a human-readable feedback string from training verification results.

    Used as <FEEDBACK> in the reattempt prompt.
    Deduplicates repeated syntax errors (all examples share the same parse error).
    """
    # Deduplicate clingo_error across examples (same error shown once)
    all_clingo_errors = [
        r["clingo_errors"]
        for r in train_results
        if r["status"] == "clingo_error" and not r["correct"]
    ]
    shared_error = (
        all_clingo_errors[0]
        if all_clingo_errors
        and len(set(all_clingo_errors)) == 1
        and len(all_clingo_errors) > 1
        else None
    )

    if shared_error is not None:
        annotated = _annotate_clingo_error(shared_error)
        n_examples = len(train_results)
        return (
            f"All {n_examples} examples fail with the same Clingo syntax error:\n"
            f"{annotated}"
        )

    parts = []
    for res in train_results:
        i = res["example_idx"]
        status = res["status"]

        if res["correct"]:
            parts.append(f"Example #{i + 1}: CORRECT (exact match)")
            continue

        lines = [f"Example #{i + 1}: INCORRECT"]

        if status == "clingo_error":
            annotated = _annotate_clingo_error(res["clingo_errors"])
            lines.append(f"  Clingo error:\n{annotated}")

        elif status == "unsatisfiable":
            lines.append("  Program is UNSATISFIABLE — 0 answer sets produced.")

        elif status == "underconstrained":
            n = res["n_answer_sets"]
            diff = res.get("diff")
            acc = res.get("accuracy", 0.0)
            lines.append(
                f"  Program is UNDERCONSTRAINED — {n} answer sets produced (need exactly 1).\n"
                f"  You need more :- constraints to uniquely determine the output.\n"
                f"  One example answer set (first of {n}, accuracy {acc:.2f} vs expected):\n"
                + (diff if diff else "  (no diff available)")
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


def predict_on_test_examples(program, test_examples, pipeline):
    """Run the final program on test inputs and return predicted grids.

    Unlike verify_on_training_examples(), this function does not compare to an
    expected output (test answers are hidden in ARC-AGI). It uses the test input
    dimensions as bounding box for the predicted grid — output same size as input
    is a common ARC-AGI pattern; any atoms outside those bounds are silently dropped.

    Args:
        program: ASP program string (without input facts).
        test_examples: list of {"input": grid} dicts (output key may be absent).
        pipeline: Pipeline instance with gen_answer_set().

    Returns:
        list of result dicts, one per test example:
            {"test_idx": int, "status": str, "n_answer_sets": int,
             "clingo_errors": str, "grid_predicted": list or None}
    """
    if not test_examples:
        return []

    results = []
    for i, ex in enumerate(test_examples):
        input_grid = ex["input"]
        n_rows = len(input_grid)
        n_cols = len(input_grid[0]) if input_grid else 0

        input_facts = grid_to_input_facts(input_grid)
        full_program = input_facts + "\n\n" + program

        status, answer_sets_or_errors = pipeline.gen_answer_set(full_program)

        result = {"test_idx": i}

        if status is RuntimeError:
            errors = "\n".join(str(x[1]).strip() for x in answer_sets_or_errors)
            result.update(
                status="clingo_error",
                n_answer_sets=0,
                clingo_errors=errors,
                grid_predicted=None,
            )
            logger.debug(f"  Test example {i}: Clingo error — {errors[:120]}")

        elif len(answer_sets_or_errors) == 0:
            result.update(
                status="unsatisfiable",
                n_answer_sets=0,
                clingo_errors="0 answer sets (unsatisfiable)",
                grid_predicted=None,
            )
            logger.debug(f"  Test example {i}: unsatisfiable")

        elif len(answer_sets_or_errors) != 1:
            n = len(answer_sets_or_errors)
            first_atoms = answer_sets_or_errors[0]
            first_predicted = answer_set_to_grid(first_atoms, n_rows, n_cols)
            result.update(
                status="underconstrained",
                n_answer_sets=n,
                clingo_errors=f"{n} answer sets found (expected exactly 1)",
                grid_predicted=first_predicted,
            )
            logger.debug(f"  Test example {i}: {n} answer sets (underconstrained)")

        else:
            atoms = answer_sets_or_errors[0]
            predicted = answer_set_to_grid(atoms, n_rows, n_cols)
            result.update(
                status="predicted",
                n_answer_sets=1,
                clingo_errors="",
                grid_predicted=predicted,
            )
            logger.debug(f"  Test example {i}: predicted ({n_rows}x{n_cols} grid)")

        results.append(result)

    return results


def all_correct(train_results):
    """Return True if every training example was solved correctly."""
    return all(r["correct"] for r in train_results)
