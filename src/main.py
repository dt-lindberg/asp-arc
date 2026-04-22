"""
Baseline orchestrator.

* Loads a batch of ARC puzzles, asks the Agent for an initial ASP program,
  verifies via Clingo on each puzzle's training examples, and runs a short
  refinement loop for puzzles that don't yet pass.
* No prompting pipeline, no cache, no tool use — the Agent only generates
  ASP text; Clingo is invoked here.
* Per-puzzle records are written atomically to audit/<run_id>/<puzzle_id>.json
  after each attempt so results survive mid-run crashes.
"""

import argparse
import os
import time

from agent.agent import Agent
from config.config import MAX_ATTEMPTS, SEED
from utils.arc_loader import get_puzzles, get_puzzles_by_ids
from utils.clingo import run_clingo
from utils.eval import verify_on_training_examples
from utils.logger import setup_logging, get_logger
from utils.session import Session

setup_logging(log_level=os.getenv("LOG_LEVEL", "info"))
logger = get_logger(__name__)


def main(args):
    run_id = time.strftime("%Y%m%d_%H%M%S")
    logger.info(f"Run ID: {run_id}")
    logger.info(f"Args: {vars(args)}")

    # Load selected IDs or choose n at random
    if args.puzzle_ids:
        puzzles = get_puzzles_by_ids(args.puzzle_ids, dataset=args.dataset)
    else:
        puzzles = get_puzzles(dataset=args.dataset, n=args.num, seed=SEED)

    logger.info(f"Loaded {len(puzzles)} puzzle(s)")
    logger.debug(f"IDs: {[p['id'] for p in puzzles]}")

    # Create per-run audit directory; sessions write themselves there incrementally
    audit_dir = os.path.join("audit", run_id)
    os.makedirs(audit_dir, exist_ok=True)

    agent = Agent()
    sessions = [
        Session(p, run_id, audit_path=os.path.join(audit_dir, f"{p['id']}.json"))
        for p in puzzles
    ]

    _run(agent, puzzles, sessions)

    n_final = sum(s.final_correct for s in sessions)
    logger.info(
        f"Final: {n_final}/{len(sessions)} puzzle(s) solved (all training examples correct)"
    )


def _run(agent, puzzles, sessions):
    # Initial attempt
    logger.info(f"Generating initial programs for {len(puzzles)} puzzle(s)...")
    initial = agent.generate_initial(puzzles)

    for puzzle, sess, (prompt, thinking, response, program) in zip(
        puzzles, sessions, initial
    ):
        logger.debug(f"  [{puzzle['id']}] verifying initial program on Clingo...")
        train_results = verify_on_training_examples(
            program, puzzle["train"], run_clingo
        )
        sess.record_initial(prompt, thinking, response, program, train_results)
        n_correct = sum(r["correct"] for r in train_results)
        logger.debug(
            f"  [{puzzle['id']}] initial: {n_correct}/{len(train_results)} correct"
        )

    n_solved = sum(s.all_train_correct for s in sessions)
    logger.info(
        f"After initial attempt: {n_solved}/{len(sessions)} puzzles pass all training examples"
    )

    # Refinement loop
    for attempt in range(1, MAX_ATTEMPTS + 1):
        active = [(p, s) for p, s in zip(puzzles, sessions) if not s.all_train_correct]
        if not active:
            logger.info(
                f"All puzzles solved, stopping after {attempt - 1} refinement(s)"
            )
            break

        logger.info(
            f"Refinement attempt {attempt}/{MAX_ATTEMPTS}: {len(active)} active puzzle(s)"
        )
        active_puzzles = [p for p, _ in active]
        active_histories = [s.history for _, s in active]
        results = agent.generate_reattempt(active_puzzles, active_histories)

        for (puzzle, sess), (prompt, thinking, response, program) in zip(
            active, results
        ):
            logger.debug(f"  [{puzzle['id']}] verifying refined program on Clingo...")
            train_results = verify_on_training_examples(
                program, puzzle["train"], run_clingo
            )
            sess.record_refinement(
                attempt, prompt, thinking, response, program, train_results
            )
            n_correct = sum(r["correct"] for r in train_results)
            is_correct = sess.all_train_correct
            logger.debug(
                f"  [{puzzle['id']}] attempt {attempt}: {n_correct}/{len(train_results)} correct"
                + (" — SOLVED" if is_correct else "")
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ARC-ASP baseline")
    parser.add_argument(
        "--dataset", default="arc-v1-training", help="Dataset to sample from"
    )
    parser.add_argument("--num", default=3, type=int, help="Number of puzzles to run")
    parser.add_argument(
        "--puzzle_ids",
        nargs="+",
        default=None,
        help="Specific puzzle IDs to run (overrides --num)",
    )
    parser.add_argument(
        "--seed", default=SEED, type=int, help="Seed for everything, including vLLM"
    )
    args = parser.parse_args()
    main(args)
