"""Entry point for the single-prompt Nemotron experiment.

Replaces the 4-step decomposed pipeline with a single LLM call per puzzle.
The model receives the puzzle examples and a single prompt; it is expected to
reason internally and produce a complete ASP program in one shot.

The refinement loop is identical to main.py: failed programs are re-submitted
with accumulated Clingo feedback appended (prompts/5_reattempt.txt).

Results are saved as JSON to outputs/<run_id>.json with the same schema as
main.py, using a single 'generation' key in the steps dict.
"""

import argparse
import json
import os
import time
from dotenv import load_dotenv

from arc_loader import get_puzzles, get_puzzles_by_ids
from eval import verify_on_training_examples, build_train_feedback, all_correct
from utils import format_examples_for_prompt, extract_code_blocks
from logger import setup_logging, get_logger
from config import MAX_ATTEMPTS, MAX_MODEL_LEN, SEED

load_dotenv()
setup_logging(log_level=os.getenv("LOG_LEVEL", "info"))
logger = get_logger(__name__)

# Prompt paths for this experiment
SINGLE_STEP_PROMPT = "prompts/single_step.txt"
REATTEMPT_PROMPT = "prompts/5_reattempt.txt"

# Nemotron sampling config
MAX_TOKENS = 8192
TEMPERATURE = 0.7


# ---------------------------------------------------------------------------
# Engine singleton
# ---------------------------------------------------------------------------

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from nemotron_engine import NemotronEngine
        _engine = NemotronEngine(max_tokens=MAX_TOKENS, temperature=TEMPERATURE)
    return _engine


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def _load_prompt(path):
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def _build_reattempt_prompt(system_prompt, instruction, formatted_examples, history):
    """Build the reattempt prompt for one puzzle.

    Programs stored in history may be large (e.g. the full LLM response if no
    code block was extracted). Cap each to MAX_HISTORY_PROGRAM_CHARS to keep
    the total prompt within Nemotron's context window.
    """
    # Nemotron context budget: max_model_len=16384, max_tokens=4096 output.
    # Chars-per-token ratio is ~1.0 for this model on ASP/technical content.
    # When extract_code_blocks() fails (no fences found), 'program' and 'feedback'
    # can be huge (20K+ chars of LLM prose + thousands of Clingo parse errors).
    # Cap both aggressively so the reattempt prompt stays within context.
    MAX_HISTORY_PROGRAM_CHARS = 3000
    MAX_HISTORY_FEEDBACK_CHARS = 2000

    history_parts = []
    for idx, (program, feedback) in enumerate(history, start=1):
        if len(program) > MAX_HISTORY_PROGRAM_CHARS:
            program = program[:MAX_HISTORY_PROGRAM_CHARS] + "\n... [truncated for context budget]"
        if len(feedback) > MAX_HISTORY_FEEDBACK_CHARS:
            feedback = feedback[:MAX_HISTORY_FEEDBACK_CHARS] + "\n... [truncated]"
        history_parts.append(
            f"<attempt_{idx}>\n```asp\n{program}\n```\n\n"
            f"<feedback>\n{feedback}\n</feedback>\n</attempt_{idx}>"
        )

    history_str = "\n\n".join(history_parts) if history_parts else "(none)"

    prompt = instruction
    prompt = prompt.replace("<EXAMPLES>", formatted_examples)
    prompt = prompt.replace("<HISTORY>", history_str)
    full = system_prompt + "\n\n" + prompt

    # Secondary safety check: if still too long, truncate the oldest entry further
    char_budget = 13000  # chars; Nemotron 16384-token context, ~1.1 chars/token, minus output/overhead
    if len(full) > char_budget:
        logger.warning(
            f"Reattempt prompt ({len(full)} chars) exceeds budget ({char_budget}). "
            f"Truncating oldest program to 500 chars."
        )
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


# ---------------------------------------------------------------------------
# Clingo via a minimal pipeline wrapper (reuses pipeline.gen_answer_set)
# ---------------------------------------------------------------------------

class _ClingoRunner:
    """Minimal wrapper exposing gen_answer_set() from Pipeline."""

    def __init__(self):
        from pipeline import Pipeline
        self._p = Pipeline()

    def gen_answer_set(self, program):
        return self._p.gen_answer_set(program)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(args):
    run_id = time.strftime("%Y%m%d_%H%M%S")
    logger.info(f"Run ID: {run_id}")
    logger.info(f"Args: {vars(args)}")

    os.makedirs("outputs", exist_ok=True)

    # Load puzzles
    if args.puzzle_ids:
        puzzles = get_puzzles_by_ids(args.puzzle_ids, dataset=args.dataset)
    else:
        puzzles = get_puzzles(dataset=args.dataset, n=args.num, seed=SEED)

    logger.info(f"Loaded {len(puzzles)} puzzle(s): {[p['id'] for p in puzzles]}")

    # Load prompts
    single_step_tmpl = _load_prompt(SINGLE_STEP_PROMPT)
    reattempt_raw = _load_prompt(REATTEMPT_PROMPT)
    system_prompt, instruction = reattempt_raw.split("===SEPARATOR===")
    system_prompt = system_prompt.strip()
    instruction = instruction.strip()

    clingo = _ClingoRunner()
    formatted_examples = [format_examples_for_prompt(p["train"]) for p in puzzles]
    n = len(puzzles)
    records = [_make_record(p, run_id) for p in puzzles]

    try:
        _run(
            puzzles, formatted_examples, records, run_id,
            single_step_tmpl, system_prompt, instruction, clingo,
        )
    except Exception as e:
        logger.error(f"Pipeline crashed: {e}", exc_info=True)
        logger.info("Saving partial results before exiting...")
        _save_results(records, run_id + "_partial")
        raise

    return records


def _run(puzzles, formatted_examples, records, run_id,
         single_step_tmpl, system_prompt, instruction, clingo):
    n = len(puzzles)

    # ── Single-step generation ────────────────────────────────────────────
    logger.info(f"Single-step generation for {n} puzzle(s)...")

    prompts = [
        single_step_tmpl.replace("<EXAMPLES>", fe)
        for fe in formatted_examples
    ]
    messages_list = [[{"role": "user", "content": p}] for p in prompts]

    results = _get_engine().generate_batch(messages_list)

    programs = []
    for i, (thinking, response) in enumerate(results):
        extracted = extract_code_blocks(response)
        programs.append(extracted)
        records[i]["steps"]["generation"] = {
            "prompt": prompts[i],
            "thinking": thinking,
            "response": response,
            "extracted": extracted,
        }
        records[i]["full_program"] = extracted
        logger.info(
            f"  [{puzzles[i]['id']}] generated ({len(extracted)} chars ASP, "
            f"{len(thinking)} chars thinking)"
        )

    # ── Verify on training examples ───────────────────────────────────────
    logger.info("Verifying programs on training examples...")
    train_results_list = []
    for i, (puzzle, program) in enumerate(zip(puzzles, programs)):
        logger.info(f"  [{puzzle['id']}] running Clingo on {len(puzzle['train'])} example(s)...")
        t0 = time.time()
        train_results = verify_on_training_examples(program, puzzle["train"], clingo)
        elapsed = round(time.time() - t0, 2)

        records[i]["train_verifications"] = train_results
        records[i]["all_train_correct"] = all_correct(train_results)
        train_results_list.append(train_results)

        n_correct = sum(r["correct"] for r in train_results)
        logger.info(f"  [{puzzle['id']}] {n_correct}/{len(train_results)} correct in {elapsed}s")

    n_solved = sum(records[i]["all_train_correct"] for i in range(n))
    logger.info(f"After generation: {n_solved}/{n} puzzles pass all training examples")

    # ── Refinement loop ───────────────────────────────────────────────────
    histories = [[] for _ in range(n)]
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
                system_prompt, instruction, formatted_examples[i], histories[i]
            )
            for i in active
        ]

        # Debug: log prompt sizes before sending to vLLM
        for idx, (i, p) in enumerate(zip(active, raw_prompts)):
            logger.info(
                f"  Reattempt prompt [{puzzles[i]['id']}]: {len(p)} chars "
                f"(history entries: {len(histories[i])})"
            )

        # Reattempt uses raw single-turn messages
        reattempt_messages = [[{"role": "user", "content": p}] for p in raw_prompts]
        gen_results = _get_engine().generate_batch(reattempt_messages)

        for i, (thinking, response) in zip(active, gen_results):
            new_program = extract_code_blocks(response)
            programs[i] = new_program
            records[i]["full_program"] = new_program

            logger.info(f"  [{puzzles[i]['id']}] re-running Clingo...")
            t0 = time.time()
            new_train_results = verify_on_training_examples(
                new_program, puzzles[i]["train"], clingo
            )
            elapsed = round(time.time() - t0, 2)

            is_correct = all_correct(new_train_results)
            n_correct = sum(r["correct"] for r in new_train_results)
            logger.info(
                f"  [{puzzles[i]['id']}] attempt {attempt}: "
                f"{n_correct}/{len(new_train_results)} correct in {elapsed}s"
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

    # Mark puzzles solved at step 0
    for i in range(n):
        if records[i]["all_train_correct"] and not records[i]["refinements"]:
            records[i]["final_correct"] = True

    _save_results(records, run_id)

    n_final = sum(r["final_correct"] for r in records)
    logger.info(f"Final: {n_final}/{n} puzzle(s) solved (all training examples correct)")
    for r in records:
        status = "SOLVED" if r["final_correct"] else "UNSOLVED"
        n_ref = len(r["refinements"])
        logger.info(f"  {r['puzzle_id']}: {status} ({n_ref} refinement(s))")


def _save_results(records, run_id):
    out_path = os.path.join("outputs", f"{run_id}.json")
    with open(out_path, "w") as f:
        json.dump(records, f, indent=2)
    logger.info(f"Results saved to {out_path}")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ARC-ASP single-prompt Nemotron experiment")
    parser.add_argument("--dataset", default="arc-v1-training", help="Dataset to sample from")
    parser.add_argument("--num", default=3, type=int, help="Number of puzzles to run")
    parser.add_argument(
        "--puzzle_ids",
        nargs="+",
        default=None,
        help="Specific puzzle IDs to run (overrides --num)",
    )
    args = parser.parse_args()
    main(args)
