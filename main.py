"""Entry point for the ARC-ASP pipeline.

Runs a single-prompt generation (single_step) across a batch of puzzles,
verifies each program on the training examples, then runs a refinement loop
for puzzles that don't yet pass.

Results are saved as JSON to outputs/<run_id>.json.
"""

import argparse
import json
import os
import time
from dotenv import load_dotenv

from arc_loader import get_puzzles, get_puzzles_by_ids
from pipeline import Pipeline
from eval import verify_on_training_examples, build_train_feedback, all_correct
from utils import format_examples_for_prompt, extract_code_blocks
from logger import setup_logging, get_logger
from config import DEFAULT_ENGINE, MAX_ATTEMPTS, SEED

load_dotenv()
setup_logging(log_level=os.getenv("LOG_LEVEL", "info"))
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Reattempt prompt builder
# ---------------------------------------------------------------------------

def _build_reattempt_prompt(system_prompt, instruction, formatted_examples, history):
    """Build the reattempt prompt for one puzzle.

    Args:
        system_prompt: Part A of the 5_reattempt.txt template (before SEPARATOR).
        instruction:   Part B of the 5_reattempt.txt template (after SEPARATOR).
        formatted_examples: formatted training examples string.
        history: list of (program, feedback_str) pairs, oldest first.

    Returns:
        Full prompt string.
    """
    # Cap program length as a safety guard against extraction failures where the
    # full model output (potentially 100K+ chars) ends up stored as the "program".
    MAX_PROGRAM_IN_HISTORY = 8_000

    history_parts = []
    for idx, (program, feedback) in enumerate(history, start=1):
        if len(program) > MAX_PROGRAM_IN_HISTORY:
            logger.warning(
                f"History program for attempt {idx} is {len(program)} chars "
                f"(likely an extraction failure) — truncating to {MAX_PROGRAM_IN_HISTORY} chars"
            )
            program = program[:MAX_PROGRAM_IN_HISTORY] + "\n... [truncated — extraction likely failed]"
        history_parts.append(
            f"<attempt_{idx}>\n```asp\n{program}\n```\n\n"
            f"<feedback>\n{feedback}\n</feedback>\n</attempt_{idx}>"
        )

    history_str = "\n\n".join(history_parts) if history_parts else "(none)"

    prompt = instruction
    prompt = prompt.replace("<EXAMPLES>", formatted_examples)
    prompt = prompt.replace("<HISTORY>", history_str)
    return system_prompt + "\n\n" + prompt


# ---------------------------------------------------------------------------
# Per-puzzle result record
# ---------------------------------------------------------------------------

def _make_record(puzzle, run_id):
    return {
        "run_id": run_id,
        "puzzle_id": puzzle["id"],
        "dataset": puzzle["dataset"],
        "n_train_examples": len(puzzle["train"]),
        "steps": {},
        "full_program": "",
        "train_verifications": [],
        "all_train_correct": False,
        "refinements": [],
        "final_correct": False,
    }


def _record_step(record, step_name, prompt, thinking, response, extracted):
    record["steps"][step_name] = {
        "prompt": prompt,
        "thinking": thinking,
        "response": response,
        "extracted": extracted,
    }


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main(args):
    run_id = time.strftime("%Y%m%d_%H%M%S")
    logger.info(f"Run ID: {run_id}")
    logger.info(f"Args: {vars(args)}")

    # ── Load puzzles ──────────────────────────────────────────────────────
    if args.puzzle_ids:
        puzzles = get_puzzles_by_ids(args.puzzle_ids, dataset=args.dataset)
    else:
        puzzles = get_puzzles(dataset=args.dataset, n=args.num, seed=SEED)

    logger.info(f"Loaded {len(puzzles)} puzzle(s): {[p['id'] for p in puzzles]}")

    # ── Init pipeline ─────────────────────────────────────────────────────
    pipeline = Pipeline({"engine": args.engine})
    pipeline.load_prompts()
    pipeline.load_cache()

    # ── Pre-format examples for all puzzles ───────────────────────────────
    formatted_examples = [format_examples_for_prompt(p["train"]) for p in puzzles]

    records = [_make_record(p, run_id) for p in puzzles]

    try:
        _run_pipeline(puzzles, pipeline, formatted_examples, records, run_id)
    except Exception as e:
        logger.error(f"Pipeline crashed: {e}", exc_info=True)
        logger.info("Saving partial results before exiting...")
        _save_results(records, run_id + "_partial")
        raise

    return records


def _run_pipeline(puzzles, pipeline, formatted_examples, records, run_id):
    n = len(puzzles)

    # ──────────────────────────────────────────────────────────────────────
    # Single-step generation
    # ──────────────────────────────────────────────────────────────────────
    logger.info(f"Single-step generation for {n} puzzle(s)...")
    replaces = [{"<EXAMPLES>": fe} for fe in formatted_examples]
    results = pipeline.gen_response_batch("single_step", replaces)

    programs = []
    for i, (thinking, response) in enumerate(results):
        extracted = extract_code_blocks(response)
        programs.append(extracted)
        prompt_used = pipeline.prompt["single_step"].replace("<EXAMPLES>", formatted_examples[i])
        _record_step(records[i], "generation", prompt_used, thinking, response, extracted)
        logger.info(
            f"  [{puzzles[i]['id']}] generated ({len(extracted)} chars ASP, "
            f"{len(thinking)} chars thinking)"
        )

    # ── Assemble full programs ────────────────────────────────────────────
    for i in range(n):
        records[i]["full_program"] = programs[i]

    # ──────────────────────────────────────────────────────────────────────
    # Verify on training examples (Clingo, sequential)
    # ──────────────────────────────────────────────────────────────────────
    logger.info("Verifying programs on training examples...")
    train_results_list = []
    for i, (puzzle, program) in enumerate(zip(puzzles, programs)):
        logger.info(f"  [{puzzle['id']}] running Clingo on {len(puzzle['train'])} example(s)...")
        t0 = time.time()
        train_results = verify_on_training_examples(program, puzzle["train"], pipeline)
        elapsed = round(time.time() - t0, 2)

        records[i]["train_verifications"] = train_results
        records[i]["all_train_correct"] = all_correct(train_results)
        train_results_list.append(train_results)

        n_correct = sum(r["correct"] for r in train_results)
        logger.info(
            f"  [{puzzle['id']}] {n_correct}/{len(train_results)} correct in {elapsed}s"
        )

    n_solved = sum(records[i]["all_train_correct"] for i in range(n))
    logger.info(f"After generation: {n_solved}/{n} puzzles pass all training examples")

    # ──────────────────────────────────────────────────────────────────────
    # Refinement loop
    # ──────────────────────────────────────────────────────────────────────
    system_prompt, instruction = pipeline.prompt["reattempt"].split("===SEPARATOR===")
    system_prompt = system_prompt.strip()
    instruction = instruction.strip()

    # Per-puzzle history: list of (program, feedback_str)
    histories = [[] for _ in range(n)]

    # Seed history with the initial attempt + feedback
    for i in range(n):
        if not records[i]["all_train_correct"]:
            feedback = build_train_feedback(train_results_list[i])
            histories[i].append((programs[i], feedback))

    for attempt in range(1, MAX_ATTEMPTS + 1):
        active = [i for i in range(n) if not records[i]["all_train_correct"]]
        if not active:
            logger.info(f"All puzzles solved — stopping after {attempt - 1} refinement(s)")
            break

        logger.info(
            f"Refinement attempt {attempt}/{MAX_ATTEMPTS}: {len(active)} active puzzle(s)"
        )

        raw_prompts = [
            _build_reattempt_prompt(
                system_prompt,
                instruction,
                formatted_examples[i],
                histories[i],
            )
            for i in active
        ]

        gen_results = pipeline.gen_response_raw_batch("reattempt", raw_prompts)

        for i, (thinking, response) in zip(active, gen_results):
            new_program = extract_code_blocks(response)
            programs[i] = new_program

            logger.info(f"  [{puzzles[i]['id']}] re-running Clingo...")
            t0 = time.time()
            new_train_results = verify_on_training_examples(
                new_program, puzzles[i]["train"], pipeline
            )
            elapsed = round(time.time() - t0, 2)

            is_correct = all_correct(new_train_results)
            n_correct = sum(r["correct"] for r in new_train_results)
            logger.info(
                f"  [{puzzles[i]['id']}] attempt {attempt}: {n_correct}/{len(new_train_results)} correct in {elapsed}s"
            )

            refinement_entry = {
                "attempt": attempt,
                "prompt": raw_prompts[active.index(i)],
                "thinking": thinking,
                "response": response,
                "program": new_program,
                "train_verifications": new_train_results,
                "all_train_correct": is_correct,
            }
            records[i]["refinements"].append(refinement_entry)

            if is_correct:
                records[i]["all_train_correct"] = True
                records[i]["final_correct"] = True
                logger.info(f"  [{puzzles[i]['id']}] SOLVED at attempt {attempt}")
            else:
                feedback = build_train_feedback(new_train_results)
                histories[i].append((new_program, feedback))

    # Mark final correctness for puzzles solved on first attempt
    for i in range(n):
        if records[i]["all_train_correct"] and not records[i]["refinements"]:
            records[i]["final_correct"] = True

    # ──────────────────────────────────────────────────────────────────────
    # Save results
    # ──────────────────────────────────────────────────────────────────────
    _save_results(records, run_id)

    n_final = sum(r["final_correct"] for r in records)
    logger.info(f"Final: {n_final}/{n} puzzle(s) solved (all training examples correct)")
    for r in records:
        status = "SOLVED" if r["final_correct"] else "UNSOLVED"
        n_ref = len(r["refinements"])
        logger.info(f"  {r['puzzle_id']}: {status} ({n_ref} refinement(s))")

    return records


def _save_results(records, run_id):
    out_path = os.path.join("outputs", f"{run_id}.json")
    with open(out_path, "w") as f:
        json.dump(records, f, indent=2)
    logger.info(f"Results saved to {out_path}")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ARC-ASP pipeline")
    parser.add_argument("--dataset", default="arc-v1-training", help="Dataset to sample from")
    parser.add_argument("--num", default=3, type=int, help="Number of puzzles to run")
    parser.add_argument(
        "--puzzle_ids",
        nargs="+",
        default=None,
        help="Specific puzzle IDs to run (overrides --num)",
    )
    parser.add_argument("--engine", default=DEFAULT_ENGINE, help="Engine label for cache naming")
    args = parser.parse_args()
    main(args)
