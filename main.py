"""Entry point for the ARC-ASP pipeline.

Runs the 4-step decomposed generation (constants → predicates → choice_rules →
constraints) across a batch of puzzles, verifies each program on the training
examples, then runs a refinement loop for puzzles that don't yet pass.

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
from utils import (
    format_examples_for_prompt,
    extract_code_blocks,
)
from logger import setup_logging, get_logger
from config import (
    DEFAULT_ENGINE,
    TEMPERATURE,
    MAX_TOKENS,
    MAX_ATTEMPTS,
    MAX_MODEL_LEN,
    SEED,
)

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
    history_parts = []
    for idx, (program, feedback) in enumerate(history, start=1):
        history_parts.append(
            f"<attempt_{idx}>\n```asp\n{program}\n```\n\n"
            f"<feedback>\n{feedback}\n</feedback>\n</attempt_{idx}>"
        )

    history_str = "\n\n".join(history_parts) if history_parts else "(none)"

    prompt = instruction
    prompt = prompt.replace("<EXAMPLES>", formatted_examples)
    prompt = prompt.replace("<HISTORY>", history_str)
    full = system_prompt + "\n\n" + prompt

    # Guard against context overflow: if prompt is too long, truncate the oldest
    # program in the history (keep feedback, shorten code).
    # Rough estimate: 1 token ≈ 4 chars; leave MAX_TOKENS budget for output.
    char_budget = (MAX_MODEL_LEN - MAX_TOKENS) * 4
    if len(full) > char_budget and len(history) > 1:
        logger.warning(
            f"Reattempt prompt ({len(full)} chars) may exceed context budget "
            f"({char_budget} chars). Truncating oldest program in history."
        )
        # Replace the oldest full program with a [truncated] placeholder
        old_prog, old_feedback = history[0]
        truncated_prog = old_prog[:500] + "\n... [truncated for context budget]"
        history_parts[0] = (
            f"<attempt_1>\n```asp\n{truncated_prog}\n```\n\n"
            f"<feedback>\n{old_feedback}\n</feedback>\n</attempt_1>"
        )
        history_str = "\n\n".join(history_parts)
        prompt = instruction
        prompt = prompt.replace("<EXAMPLES>", formatted_examples)
        prompt = prompt.replace("<HISTORY>", history_str)
        full = system_prompt + "\n\n" + prompt

    return full


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
    pipeline = Pipeline({"engine": args.engine, "temperature": args.temperature, "max_tokens": args.max_tokens})
    pipeline.load_prompts()
    pipeline.load_cache()

    # ── Pre-format examples for all puzzles ───────────────────────────────
    formatted_examples = [format_examples_for_prompt(p["train"]) for p in puzzles]
    n = len(puzzles)

    records = [_make_record(p, run_id) for p in puzzles]

    try:
        _run_pipeline(args, puzzles, pipeline, formatted_examples, records, run_id)
    except Exception as e:
        logger.error(f"Pipeline crashed: {e}", exc_info=True)
        logger.info("Saving partial results before exiting...")
        _save_results(records, run_id + "_partial")
        raise

    return records


def _run_pipeline(args, puzzles, pipeline, formatted_examples, records, run_id):
    n = len(puzzles)

    # ──────────────────────────────────────────────────────────────────────
    # Step 1: Constants
    # ──────────────────────────────────────────────────────────────────────
    logger.info(f"Step 1: generating constants for {n} puzzle(s)...")
    replaces_1 = [{"<EXAMPLES>": fe} for fe in formatted_examples]
    results_1 = pipeline.gen_response_batch("constants", replaces_1)

    constants_list = []
    for i, (thinking, response) in enumerate(results_1):
        extracted = extract_code_blocks(response)
        constants_list.append(extracted)
        prompt_used = pipeline.prompt["constants"].replace("<EXAMPLES>", formatted_examples[i])
        _record_step(records[i], "constants", prompt_used, thinking, response, extracted)
        logger.info(f"  [{puzzles[i]['id']}] constants extracted ({len(extracted)} chars)")

    # ──────────────────────────────────────────────────────────────────────
    # Step 2: Predicates
    # ──────────────────────────────────────────────────────────────────────
    logger.info(f"Step 2: generating predicates for {n} puzzle(s)...")
    replaces_2 = [
        {"<EXAMPLES>": fe, "<CONSTANTS>": c}
        for fe, c in zip(formatted_examples, constants_list)
    ]
    results_2 = pipeline.gen_response_batch("predicates", replaces_2)

    predicates_list = []
    for i, (thinking, response) in enumerate(results_2):
        extracted = extract_code_blocks(response)
        predicates_list.append(extracted)
        prompt_used = (
            pipeline.prompt["predicates"]
            .replace("<EXAMPLES>", formatted_examples[i])
            .replace("<CONSTANTS>", constants_list[i])
        )
        _record_step(records[i], "predicates", prompt_used, thinking, response, extracted)
        logger.info(f"  [{puzzles[i]['id']}] predicates extracted ({len(extracted)} chars)")

    # ──────────────────────────────────────────────────────────────────────
    # Step 3: Choice rules
    # ──────────────────────────────────────────────────────────────────────
    logger.info(f"Step 3: generating choice rules for {n} puzzle(s)...")
    replaces_3 = [
        {"<EXAMPLES>": fe, "<CONSTANTS>": c, "<PREDICATES>": p}
        for fe, c, p in zip(formatted_examples, constants_list, predicates_list)
    ]
    results_3 = pipeline.gen_response_batch("choice_rules", replaces_3)

    choice_rules_list = []
    for i, (thinking, response) in enumerate(results_3):
        extracted = extract_code_blocks(response)
        choice_rules_list.append(extracted)
        prompt_used = (
            pipeline.prompt["choice_rules"]
            .replace("<EXAMPLES>", formatted_examples[i])
            .replace("<CONSTANTS>", constants_list[i])
            .replace("<PREDICATES>", predicates_list[i])
        )
        _record_step(records[i], "choice_rules", prompt_used, thinking, response, extracted)
        logger.info(f"  [{puzzles[i]['id']}] choice rules extracted ({len(extracted)} chars)")

    # ──────────────────────────────────────────────────────────────────────
    # Step 4: Constraints
    # ──────────────────────────────────────────────────────────────────────
    logger.info(f"Step 4: generating constraints for {n} puzzle(s)...")
    replaces_4 = [
        {"<EXAMPLES>": fe, "<CONSTANTS>": c, "<PREDICATES>": p, "<CHOICE_RULES>": cr}
        for fe, c, p, cr in zip(
            formatted_examples, constants_list, predicates_list, choice_rules_list
        )
    ]
    results_4 = pipeline.gen_response_batch("constraints", replaces_4)

    constraints_list = []
    for i, (thinking, response) in enumerate(results_4):
        extracted = extract_code_blocks(response)
        constraints_list.append(extracted)
        prompt_used = (
            pipeline.prompt["constraints"]
            .replace("<EXAMPLES>", formatted_examples[i])
            .replace("<CONSTANTS>", constants_list[i])
            .replace("<PREDICATES>", predicates_list[i])
            .replace("<CHOICE_RULES>", choice_rules_list[i])
        )
        _record_step(records[i], "constraints", prompt_used, thinking, response, extracted)
        logger.info(f"  [{puzzles[i]['id']}] constraints extracted ({len(extracted)} chars)")

    # ──────────────────────────────────────────────────────────────────────
    # Assemble full programs
    # ──────────────────────────────────────────────────────────────────────
    programs = []
    for i in range(n):
        parts = []
        if constants_list[i]:
            parts.append(f"% === Constants ===\n{constants_list[i]}")
        if predicates_list[i]:
            parts.append(f"% === Auxiliary predicates ===\n{predicates_list[i]}")
        if choice_rules_list[i]:
            parts.append(f"% === Choice rules ===\n{choice_rules_list[i]}")
        if constraints_list[i]:
            parts.append(f"% === Constraints ===\n{constraints_list[i]}")
        program = "\n\n".join(parts)
        programs.append(program)
        records[i]["full_program"] = program

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
    logger.info(f"After step 4: {n_solved}/{n} puzzles pass all training examples")

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

        # Build raw prompts for all active puzzles
        raw_prompts = []
        for i in active:
            raw_prompts.append(
                _build_reattempt_prompt(
                    system_prompt,
                    instruction,
                    formatted_examples[i],
                    histories[i],
                )
            )

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

    # Mark final correctness for puzzles solved at step 0
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
    parser.add_argument("--temperature", default=TEMPERATURE, type=float)
    parser.add_argument("--max_tokens", default=MAX_TOKENS, type=int)
    args = parser.parse_args()
    main(args)
