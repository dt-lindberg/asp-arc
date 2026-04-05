"""Entry point for the ARC-ASP pipeline.

Runs a single-prompt generation (single_step) across a batch of puzzles,
verifies each program on the training examples, then runs a refinement loop
for puzzles that don't yet pass.

Results are saved as JSON to results/<run_id>.json.
"""

import argparse
import asyncio
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

from arc_loader import get_puzzles, get_puzzles_by_ids
from pipeline import Pipeline
from eval import (
    verify_on_training_examples,
    predict_on_test_examples,
    build_train_feedback,
    all_correct,
    _check_syntax as _check_syntax_fn,
)
from utils import format_examples_for_prompt, extract_code_blocks
from agent import run_syntax_agent, quick_syntax_fix, rewrite_syntax_fix, async_rewrite_syntax_fix
from logger import setup_logging, get_logger
from config import DEFAULT_ENGINE, MAX_ATTEMPTS, MAX_SYNTAX_ATTEMPTS, SEED, N_CANDIDATES

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
            program = (
                program[:MAX_PROGRAM_IN_HISTORY]
                + "\n... [truncated — extraction likely failed]"
            )
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
        "n_test_examples": len(puzzle.get("test", [])),
        "steps": {},
        "full_program": "",
        "candidates": [],
        "train_verifications": [],
        "all_train_correct": False,
        "syntax_agent": None,
        "refinements": [],
        "final_correct": False,
        "test_predictions": [],
        "test_verifications": [],
        "test_correct": False,
    }


def _record_step(record, step_name, prompt, thinking, response, extracted):
    record["steps"][step_name] = {
        "prompt": prompt,
        "thinking": thinking,
        "response": response,
        "extracted": extracted,
    }


# ---------------------------------------------------------------------------
# Async refinement loop
# ---------------------------------------------------------------------------


async def _run_refinement_async(
    puzzles, async_engine, pipeline, formatted_examples, records, histories,
    system_prompt, instruction, programs
):
    """Run the refinement loop for all puzzles concurrently.

    Each puzzle is an independent asyncio coroutine that loops through its own
    MAX_ATTEMPTS refinement attempts. All coroutines run concurrently via
    asyncio.gather(), so their vLLM requests land in the same batch. Clingo
    verification runs in a ThreadPoolExecutor so it never blocks the event loop.

    Args:
        puzzles:            List of puzzle dicts.
        async_engine:       AsyncNemotronEngine instance.
        pipeline:           Pipeline instance (for cache and Clingo).
        formatted_examples: Pre-formatted training example strings, one per puzzle.
        records:            Per-puzzle result dicts (mutated in place).
        histories:          Per-puzzle (program, feedback) history lists (mutated).
        system_prompt:      Part A of the reattempt prompt template.
        instruction:        Part B of the reattempt prompt template.
        programs:           Current best program per puzzle (mutated in place).
    """
    loop = asyncio.get_event_loop()
    # One thread per concurrent Clingo call; each puzzle may run one Clingo check
    # per attempt, so we need at least n × (1 + MAX_SYNTAX_RETRY) workers.
    executor = ThreadPoolExecutor(max_workers=min(64, len(puzzles) * 5))

    async def _refine_one(i):
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if records[i]["all_train_correct"]:
                return

            prompt = _build_reattempt_prompt(
                system_prompt, instruction, formatted_examples[i], histories[i]
            )

            # Awaiting here yields control so other puzzle coroutines submit their
            # requests; vLLM batches all in-flight requests automatically.
            thinking, response = await async_engine.generate_one(
                [{"role": "user", "content": prompt}]
            )
            new_program = extract_code_blocks(response)

            # Cache the response so the reattempt prompt key is stored.
            await pipeline.save_cache_async("reattempt", prompt, thinking, response)

            syntax_fixes = []

            quick_fixed, n_q = quick_syntax_fix(new_program)
            if n_q > 0:
                new_program = quick_fixed
                syntax_fixes.append({"stage": "quick_fix", "n_fixes": n_q})
                logger.info(
                    f"  [{puzzles[i]['id']}] async refinement {attempt} quick_fix: {n_q} fix(es)"
                )

            # Clingo syntax check in thread pool — non-blocking
            syntax_err = await loop.run_in_executor(
                executor, _check_syntax_fn, new_program, pipeline
            )
            if syntax_err:
                new_program, n_rw_rounds, rewrite_err = await async_rewrite_syntax_fix(
                    new_program, syntax_err, async_engine, pipeline, loop, executor,
                    max_rewrites=3
                )
                if rewrite_err is None:
                    syntax_fixes.append({"stage": "rewrite", "rounds": n_rw_rounds})
                    logger.info(
                        f"  [{puzzles[i]['id']}] async refinement {attempt} rewrite fixed "
                        f"syntax in {n_rw_rounds} round(s)"
                    )
                else:
                    syntax_fixes.append(
                        {"stage": "rewrite", "rounds": n_rw_rounds, "failed": True}
                    )
                    logger.info(
                        f"  [{puzzles[i]['id']}] async refinement {attempt} rewrite partial"
                    )

            programs[i] = new_program

            logger.info(f"  [{puzzles[i]['id']}] async attempt {attempt}: running Clingo...")

            # Clingo verification in thread pool — non-blocking
            new_train_results = await loop.run_in_executor(
                executor, verify_on_training_examples,
                new_program, puzzles[i]["train"], pipeline
            )

            is_correct = all_correct(new_train_results)
            n_correct = sum(r["correct"] for r in new_train_results)
            logger.info(
                f"  [{puzzles[i]['id']}] async attempt {attempt}: "
                f"{n_correct}/{len(new_train_results)} correct"
            )

            refinement_entry = {
                "attempt": attempt,
                "syntax_fixes": syntax_fixes,
                "prompt": prompt,
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
                logger.info(f"  [{puzzles[i]['id']}] SOLVED at async attempt {attempt}")
                return
            else:
                feedback = build_train_feedback(new_train_results)
                histories[i].append((new_program, feedback))

    await asyncio.gather(*[_refine_one(i) for i in range(len(puzzles))])
    executor.shutdown(wait=False)


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
    # Multi-candidate initial generation
    # Generate N_CANDIDATES per puzzle using diverse prompt strategies,
    # apply the full syntax-fix pipeline to each, evaluate all on training
    # examples, then pick the best via structured ranking/voting.
    # ──────────────────────────────────────────────────────────────────────
    logger.info(
        f"Multi-candidate generation ({N_CANDIDATES} candidates) for {n} puzzle(s)..."
    )

    # Strategy suffixes encourage diverse encodings from the same model.
    # Candidates beyond the defined strategies reuse "" (no hint, natural diversity
    # from temperature sampling alone).
    CAND_STRATEGIES = [
        ("default", ""),
        (
            "choice+constraints",
            "\n% Strategy hint: use Choice + Constraints "
            "(1 { output(R,C,V) : color(V) } 1 :- output_cell(R,C).) "
            "with :- constraints to eliminate wrong values.",
        ),
        (
            "deterministic",
            "\n% Strategy hint: write a fully deterministic program "
            "— no choice rules. Derive output(R,C,V) directly from input facts "
            "using only regular rules and constraints.",
        ),
        (
            "column-by-column",
            "\n% Strategy hint: compute the output column by column. "
            "Define helper predicates that reason about each column independently "
            "before assembling the full output grid.",
        ),
        (
            "row-by-row",
            "\n% Strategy hint: compute the output row by row. "
            "Define helper predicates that reason about each row independently "
            "before assembling the full output grid.",
        ),
        (
            "pattern-explicit",
            "\n% Strategy hint: first identify the transformation rule in a "
            "comment, then encode it step-by-step. Name intermediate predicates "
            "descriptively (e.g., border_cell, fill_target, connected_to).",
        ),
    ]
    # Extend to N_CANDIDATES with no-hint entries if table is shorter
    while len(CAND_STRATEGIES) < N_CANDIDATES:
        CAND_STRATEGIES.append(("default-extra", ""))

    all_cand_programs = []  # all_cand_programs[cand][puzzle]
    all_cand_results = []  # all_cand_results[cand][puzzle] = (thinking, response)

    # Submit all N_CANDIDATES × n requests in one vLLM call, eliminating 5 sequential
    # idle gaps between the 6 per-strategy calls that existed previously.
    # Layout: candidate outer-loop, puzzle inner-loop — mirrors the original indexing.
    all_replaces = [
        {"<EXAMPLES>": formatted_examples[pi] + suffix}
        for _, suffix in CAND_STRATEGIES[:N_CANDIDATES]
        for pi in range(n)
    ]
    flat_results = pipeline.gen_response_batch("single_step", all_replaces)
    all_cand_results = [flat_results[c * n : (c + 1) * n] for c in range(N_CANDIDATES)]
    all_cand_programs = [
        [extract_code_blocks(resp) for _, resp in row] for row in all_cand_results
    ]
    logger.info(
        f"  Flat batch: {N_CANDIDATES} candidates × {n} puzzles = {N_CANDIDATES * n} requests"
    )

    # ── Per-candidate syntax patching + evaluation ────────────────────────
    # Apply quick_fix + rewrite to each candidate independently before ranking.
    # This prevents syntax-fixable candidates from being penalised vs. candidates
    # that happen to avoid the error by chance.
    engine = (
        pipeline._get_engine()
    )  # load once; reused by syntax fixes and later stages

    programs = []
    best_gen_results = []  # (thinking, response) for the selected candidate
    for i in range(n):
        puzzle_id = puzzles[i]["id"]
        candidate_records = []  # for result schema

        best_prog = all_cand_programs[0][i]
        best_gen = all_cand_results[0][i]
        best_cand_idx = 0
        best_n_correct = -1
        best_accuracy = -1.0
        best_has_syntax_error = True
        best_is_solved = False

        for cand_idx in range(N_CANDIDATES):
            strategy_name, _ = CAND_STRATEGIES[cand_idx]
            prog = all_cand_programs[cand_idx][i]
            fix_stages = []

            # Stage 1: cheap deterministic regex fixes
            quick_prog, n_q = quick_syntax_fix(prog)
            if n_q > 0:
                prog = quick_prog
                fix_stages.append("quick_fix")
                logger.info(
                    f"  [{puzzle_id}] cand {cand_idx}: quick_fix applied {n_q} fix(es)"
                )

            # Stage 2: single-shot LLM rewrite if still broken
            syntax_err = _check_syntax_fn(prog, pipeline)
            syntax_broken = False
            if syntax_err:
                rewritten, n_rw, rewrite_err = rewrite_syntax_fix(
                    prog, syntax_err, engine, pipeline, max_rewrites=3
                )
                if rewrite_err is None:
                    prog = rewritten
                    fix_stages.append("rewrite")
                    logger.info(
                        f"  [{puzzle_id}] cand {cand_idx}: rewrite fixed syntax "
                        f"in {n_rw} round(s)"
                    )
                else:
                    prog = rewritten  # use best partial fix
                    fix_stages.append("rewrite_partial")
                    syntax_broken = True
                    logger.info(
                        f"  [{puzzle_id}] cand {cand_idx}: rewrite partial — "
                        f"flagged syntax_broken"
                    )

            # Evaluate on training examples
            cand_results = verify_on_training_examples(
                prog, puzzles[i]["train"], pipeline
            )
            n_correct = sum(r["correct"] for r in cand_results)
            avg_acc = sum(r["accuracy"] for r in cand_results) / max(
                len(cand_results), 1
            )
            has_syntax = any(r["status"] == "clingo_error" for r in cand_results)
            is_solved = all_correct(cand_results)

            candidate_records.append(
                {
                    "idx": cand_idx,
                    "strategy": strategy_name,
                    "syntax_ok": not syntax_broken and not has_syntax,
                    "syntax_fix_stages": fix_stages,
                    "n_correct": n_correct,
                    "avg_accuracy": round(avg_acc, 4),
                    "is_solved": is_solved,
                    "selected": False,
                }
            )

            # Ranking: solved > no-syntax > highest accuracy
            is_better = (
                (is_solved and not best_is_solved)
                or (not has_syntax and best_has_syntax_error and not best_is_solved)
                or (
                    not has_syntax
                    and not best_has_syntax_error
                    and not best_is_solved
                    and avg_acc > best_accuracy
                )
                or (has_syntax and best_has_syntax_error and n_correct > best_n_correct)
            )
            if is_better:
                best_prog = prog
                best_gen = all_cand_results[cand_idx][i]
                best_cand_idx = cand_idx
                best_n_correct = n_correct
                best_accuracy = avg_acc
                best_has_syntax_error = has_syntax
                best_is_solved = is_solved
                logger.info(
                    f"  [{puzzle_id}] candidate {cand_idx} ({strategy_name}) is best so far "
                    f"(n_correct={n_correct}, acc={avg_acc:.2f}, "
                    f"syntax_err={has_syntax}, solved={is_solved})"
                )
            if is_solved:
                break

        # Mark the selected candidate in the schema
        candidate_records[best_cand_idx]["selected"] = True

        programs.append(best_prog)
        best_gen_results.append(best_gen)
        records[i]["candidates"] = candidate_records

        prompt_used = pipeline.prompt["single_step"].replace(
            "<EXAMPLES>", formatted_examples[i]
        )
        _record_step(
            records[i], "generation", prompt_used, best_gen[0], best_gen[1], best_prog
        )
        logger.info(
            f"  [{puzzle_id}] selected best candidate "
            f"({len(best_prog)} chars ASP, acc={best_accuracy:.2f})"
        )

    # ── Assemble full programs ────────────────────────────────────────────
    for i in range(n):
        records[i]["full_program"] = programs[i]

    # ──────────────────────────────────────────────────────────────────────
    # Verify best programs on training examples (final verification)
    # ──────────────────────────────────────────────────────────────────────
    logger.info("Final verification of best candidate programs...")
    train_results_list = []
    for i, (puzzle, program) in enumerate(zip(puzzles, programs)):
        logger.info(
            f"  [{puzzle['id']}] running Clingo on {len(puzzle['train'])} example(s)..."
        )
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
    # Syntax-fix agent stage
    # Run the agentic loop for any puzzle whose program has syntax errors.
    # The loop runs sequentially (one puzzle at a time) since each conversation
    # is multi-turn and cannot be batched.
    # ──────────────────────────────────────────────────────────────────────
    # engine is already loaded in the multi-candidate generation block above

    for i in range(n):
        train_results = train_results_list[i]
        has_syntax_error = (
            bool(train_results) and train_results[0]["status"] == "clingo_error"
        )

        if not has_syntax_error:
            records[i]["syntax_agent"] = {"triggered": False}
            continue

        syntax_error = train_results[0]["clingo_errors"]

        # First: try cheap deterministic regex fixes before the LLM syntax agent
        quick_fixed, n_quick = quick_syntax_fix(programs[i])
        if n_quick > 0:
            logger.info(f"  [{puzzles[i]['id']}] quick_fix applied {n_quick} fix(es)")
            quick_err = _check_syntax_fn(quick_fixed, pipeline)
            if quick_err is None:
                logger.info(
                    f"  [{puzzles[i]['id']}] quick_fix resolved all syntax errors!"
                )
                programs[i] = quick_fixed
                records[i]["syntax_agent"] = {
                    "triggered": False,
                    "quick_fix_applied": n_quick,
                }
                new_train_results = verify_on_training_examples(
                    quick_fixed, puzzles[i]["train"], pipeline
                )
                records[i]["train_verifications"] = new_train_results
                records[i]["all_train_correct"] = all_correct(new_train_results)
                train_results_list[i] = new_train_results
                continue
            # Quick fix helped but didn't fully resolve — continue with fixed version
            programs[i] = quick_fixed
            syntax_error = quick_err
            logger.info(
                f"  [{puzzles[i]['id']}] quick_fix partial, remaining: {syntax_error[:120]}"
            )

        # Stage 2: single-shot LLM rewrite (faster than multi-turn agent)
        logger.info(
            f"  [{puzzles[i]['id']}] syntax error — trying single-shot rewrite (3 attempts)..."
        )
        rewritten, n_rewrite_rounds, rewrite_err = rewrite_syntax_fix(
            program=programs[i],
            syntax_error=syntax_error,
            engine=engine,
            pipeline=pipeline,
            max_rewrites=3,
        )

        syntax_agent_record = {
            "triggered": True,
            "initial_error": syntax_error,
            "rewrite_rounds": n_rewrite_rounds,
        }

        if rewrite_err is None:
            # Rewrite resolved all syntax errors
            logger.info(
                f"  [{puzzles[i]['id']}] rewrite fixed syntax in {n_rewrite_rounds} round(s)!"
            )
            programs[i] = rewritten
            records[i]["syntax_agent"] = {
                **syntax_agent_record,
                "syntax_fixed": True,
                "steps": [],
            }
            new_train_results = verify_on_training_examples(
                rewritten, puzzles[i]["train"], pipeline
            )
            records[i]["train_verifications"] = new_train_results
            records[i]["all_train_correct"] = all_correct(new_train_results)
            records[i]["full_program"] = rewritten
            train_results_list[i] = new_train_results
            continue

        # Rewrite didn't fully fix — fall back to multi-turn tool agent
        programs[i] = rewritten
        syntax_error = rewrite_err
        logger.info(
            f"  [{puzzles[i]['id']}] rewrite partial — falling back to tool agent "
            f"(max {MAX_SYNTAX_ATTEMPTS} round(s))..."
        )

        fixed_program, syntax_steps = run_syntax_agent(
            program=programs[i],
            syntax_error=syntax_error,
            system_prompt=pipeline.prompt["syntax_agent"],
            engine=engine,
            pipeline=pipeline,
            max_attempts=MAX_SYNTAX_ATTEMPTS,
        )

        # Re-verify the (possibly fixed) program
        logger.info(f"  [{puzzles[i]['id']}] re-verifying after syntax agent...")
        t0 = time.time()
        new_train_results = verify_on_training_examples(
            fixed_program, puzzles[i]["train"], pipeline
        )
        elapsed = round(time.time() - t0, 2)

        syntax_fixed = (
            not new_train_results or new_train_results[0]["status"] != "clingo_error"
        )
        n_correct = sum(r["correct"] for r in new_train_results)
        logger.info(
            f"  [{puzzles[i]['id']}] post-syntax: {n_correct}/{len(new_train_results)} correct "
            f"in {elapsed}s | syntax_fixed={syntax_fixed}"
        )

        records[i]["syntax_agent"] = {
            **syntax_agent_record,
            "syntax_fixed": syntax_fixed,
            "steps": syntax_steps,
        }

        # Update all pipeline state for this puzzle
        programs[i] = fixed_program
        records[i]["full_program"] = fixed_program
        records[i]["train_verifications"] = new_train_results
        records[i]["all_train_correct"] = all_correct(new_train_results)
        train_results_list[i] = new_train_results

    n_solved = sum(records[i]["all_train_correct"] for i in range(n))
    logger.info(f"After syntax fix: {n_solved}/{n} puzzles pass all training examples")

    # ──────────────────────────────────────────────────────────────────────
    # Async refinement loop
    # Each puzzle runs as an independent asyncio coroutine so that their
    # LLM requests arrive at vLLM concurrently and are batched together.
    # Clingo verification runs in a ThreadPoolExecutor — non-blocking.
    # ──────────────────────────────────────────────────────────────────────
    system_prompt, instruction = pipeline.prompt["reattempt"].split("===SEPARATOR===")
    system_prompt = system_prompt.strip()
    instruction = instruction.strip()

    # Per-puzzle history: list of (program, feedback_str)
    histories = [[] for _ in range(n)]

    # Seed history with the initial attempt + feedback for unsolved puzzles
    for i in range(n):
        if not records[i]["all_train_correct"]:
            feedback = build_train_feedback(train_results_list[i])
            histories[i].append((programs[i], feedback))

    active_count = sum(1 for r in records if not r["all_train_correct"])
    if active_count > 0:
        logger.info(
            f"Starting async refinement for {active_count}/{n} unsolved puzzle(s)..."
        )
        # The sync NemotronEngine holds nearly all GPU VRAM (KV cache pre-allocated).
        # Release it completely before initialising AsyncNemotronEngine so the two
        # engines do not coexist in GPU memory at the same time.
        import gc
        import torch
        del engine
        pipeline._engine = None
        gc.collect()
        torch.cuda.empty_cache()
        logger.info("Sync engine released; loading async engine for refinement...")

        async_engine = pipeline._get_async_engine()
        asyncio.run(
            _run_refinement_async(
                puzzles, async_engine, pipeline, formatted_examples,
                records, histories, system_prompt, instruction, programs
            )
        )
    else:
        logger.info("All puzzles already solved — skipping refinement loop")

    # Mark final correctness for puzzles solved on first attempt (no refinements needed)
    for i in range(n):
        if records[i]["all_train_correct"] and not records[i]["refinements"]:
            records[i]["final_correct"] = True

    # ──────────────────────────────────────────────────────────────────────
    # Test predictions (run Clingo on all test inputs, store predicted grids)
    # ──────────────────────────────────────────────────────────────────────
    logger.info("Running test predictions on final programs...")
    for i, puzzle in enumerate(puzzles):
        test_examples = puzzle.get("test", [])
        if not test_examples:
            logger.info(f"  [{puzzle['id']}] no test examples — skipping")
            continue
        test_preds = predict_on_test_examples(programs[i], test_examples, pipeline)
        records[i]["test_predictions"] = test_preds
        n_predicted = sum(1 for p in test_preds if p["status"] == "predicted")
        logger.info(
            f"  [{puzzle['id']}] {n_predicted}/{len(test_preds)} test prediction(s) successful"
        )

    # ──────────────────────────────────────────────────────────────────────
    # Test case evaluation (if test examples have ground truth)
    # ──────────────────────────────────────────────────────────────────────
    logger.info("Evaluating final programs on test cases...")
    for i, puzzle in enumerate(puzzles):
        test_cases = [tc for tc in puzzle.get("test", []) if "output" in tc]
        if not test_cases:
            logger.info(f"  [{puzzle['id']}] no test ground truth — skipping")
            continue

        final_prog = programs[i]
        logger.info(
            f"  [{puzzle['id']}] verifying on {len(test_cases)} test case(s)..."
        )
        test_results = verify_on_training_examples(final_prog, test_cases, pipeline)
        n_test_correct = sum(r["correct"] for r in test_results)
        records[i]["test_verifications"] = test_results
        records[i]["test_correct"] = all_correct(test_results)
        logger.info(
            f"  [{puzzle['id']}] test: {n_test_correct}/{len(test_cases)} correct"
        )

    # ──────────────────────────────────────────────────────────────────────
    # Save results
    # ──────────────────────────────────────────────────────────────────────
    _save_results(records, run_id)

    n_final = sum(r["final_correct"] for r in records)
    n_test = sum(r["test_correct"] for r in records)
    logger.info(f"Final: {n_final}/{n} puzzle(s) solved (train), {n_test}/{n} (test)")
    for r in records:
        status = "SOLVED" if r["final_correct"] else "UNSOLVED"
        test_status = "TEST-PASS" if r["test_correct"] else "TEST-FAIL"
        n_ref = len(r["refinements"])
        logger.info(
            f"  {r['puzzle_id']}: {status} {test_status} ({n_ref} refinement(s))"
        )

    return records


def _save_results(records, run_id):
    out_path = os.path.join("results", f"{run_id}.json")
    with open(out_path, "w") as f:
        json.dump(records, f, indent=2)
    logger.info(f"Results saved to {out_path}")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ARC-ASP pipeline")
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
        "--engine", default=DEFAULT_ENGINE, help="Engine label for cache naming"
    )
    args = parser.parse_args()
    main(args)
