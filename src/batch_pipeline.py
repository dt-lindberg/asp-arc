"""
Batch NVARC → ASP pipeline with agentic refinement loop.

Samples N unique puzzles across all outputs/ parquets, translates each to ASP
via GPT-OSS-120B, validates with Clingo, and writes results to a JSONL file.
Failed candidates are iteratively refined by feeding Clingo error messages and
grid diffs back to the model in follow-up conversation turns.

Results are written after each chunk so intermediate progress survives a crash.
"""

import argparse
import itertools
import os
import time

from config.config import LOG_LEVEL, MAX_REFINEMENT_ATTEMPTS, SEED as DEFAULT_SEED
from config.config_llm import (
    MAX_NUM_SEQS,
    MAX_TOKENS,
    MODEL_REPO_ID,
    N_CANDIDATES,
    REASONING_EFFORT,
    TEMPERATURE,
    TOP_K,
    VLLM_HOST,
    VLLM_PORT,
)
from config.config_nvarc import NVARC_ASP_PROMPT
from llm.vllm_engine import VLLMEngine
from utils.asp_validator import validate_asp_program
from utils.logger import setup_logging, get_logger
from utils.nvarc_data import load_grid_pairs
from utils.nvarc_formatting import (
    build_prompt,
    extract_asp_block,
    extract_puzzle_xml,
    extract_python_code,
)
from utils.nvarc_sampler import sample_puzzles
from utils.output_writer import OutputWriter
from utils.refinement import (
    build_feedback_message,
    build_refinement_messages,
    categorize_first_failure,
    compute_grid_diff,
)

setup_logging(log_level=os.getenv("LOG_LEVEL", LOG_LEVEL))
logger = get_logger(__name__)


def _make_base_record(
    row,
    source_parquet,
    prompt_template,
    n_candidates,
    candidate_index,
    response,
    asp_code,
    thinking,
):
    return {
        "puzzle_name1": row.puzzle_name1,
        "puzzle_name2": row.puzzle_name2,
        "sid": int(row.sid),
        "candidate_index": candidate_index,
        "source_parquet": source_parquet,
        "prompt_template": prompt_template,
        "parameters": {
            "model": MODEL_REPO_ID,
            "reasoning_effort": REASONING_EFFORT,
            "temperature": TEMPERATURE,
            "top_k": TOP_K,
            "max_tokens": MAX_TOKENS,
            "n_candidates": n_candidates,
        },
        "thinking": thinking,
        "response": response,
        "asp_code": asp_code,
    }


def _make_initial_record(
    row,
    source_parquet,
    prompt_template,
    n_candidates,
    candidate_index,
    response,
    asp_code,
    summary,
    thinking,
):
    record = _make_base_record(
        row,
        source_parquet,
        prompt_template,
        n_candidates,
        candidate_index,
        response,
        asp_code,
        thinking,
    )
    record["validation"] = (
        None
        if summary is None
        else {
            "passed": summary["passed"],
            "correct": summary["correct"],
            "total": summary["total"],
            "clingo_errors": summary["clingo_errors"],
        }
    )
    record["refinement_round"] = 0
    return record


def _make_refinement_record(
    row,
    source_parquet,
    prompt_template,
    n_candidates,
    candidate_index,
    response,
    thinking,
    asp_code,
    summary,
    round_num,
    trigger,
    pair_idx,
    feedback_prompt,
    grid_diff,
):
    record = _make_base_record(
        row,
        source_parquet,
        prompt_template,
        n_candidates,
        candidate_index,
        response,
        asp_code,
        thinking,
    )
    record["validation"] = (
        None
        if summary is None
        else {
            "passed": summary["passed"],
            "correct": summary["correct"],
            "total": summary["total"],
            "clingo_errors": summary["clingo_errors"],
        }
    )
    record["refinement_round"] = round_num
    record["trigger"] = trigger
    record["first_failing_pair_index"] = pair_idx
    record["feedback_prompt"] = feedback_prompt
    record["grid_diff"] = grid_diff
    return record


def _try_extract_asp_block(thinking, response):
    """Extract ASP block from response (falling back to thinking)."""
    for source in (response, thinking or ""):
        if not source:
            continue
        try:
            return extract_asp_block(source), source
        except ValueError:
            logger.warning(f"Failed to extract ASP block, source={source[:100]}...")
            continue
    raise ValueError("No ```asp block found in response or thinking")


def _validate_and_write(
    writer,
    row,
    src,
    prompt_template,
    n_candidates,
    cand_idx,
    response,
    thinking,
    asp_code,
):
    """Validate asp_code and write the record. Returns (record, summary)."""
    summary = validate_asp_program(asp_code, row.puzzle_name1, row.puzzle_name2)
    record = _make_initial_record(
        row,
        src,
        prompt_template,
        n_candidates,
        cand_idx,
        response,
        asp_code,
        summary,
        thinking,
    )
    writer.write(record)
    return record, summary


def main(args):
    with open(args.prompt_template, encoding="utf-8") as f:
        template = f.read()

    writer = OutputWriter(args.output_file)
    engine = VLLMEngine(host=args.host, port=args.port, seed=args.seed)
    sampler = sample_puzzles(args.n)

    max_refinement = args.max_refinement_attempts

    n_processed = 0
    n_puzzles_passed = 0
    t0 = time.perf_counter()

    for chunk in itertools.batched(sampler, args.chunk_size):
        # ── Phase 1: Build prompts and generate initial candidates ──────────
        rows, src_parquets, initial_messages = [], [], []
        for row, src in chunk:
            try:
                puzzle_xml = extract_puzzle_xml(row.prompt)
                python_code = extract_python_code(row.completion)
                prompt = build_prompt(template, puzzle_xml, python_code)
                rows.append(row)
                src_parquets.append(src)
                initial_messages.append([{"role": "user", "content": prompt}])
            except ValueError as e:
                logger.warning(f"Skipping {row.puzzle_name1}/{row.puzzle_name2}: {e}")

        if not initial_messages:
            continue

        results = engine.generate_batch(initial_messages, n=args.n_candidates)

        # ── Phase 2: Initial validation ─────────────────────────────────────
        # Per-puzzle state: {puzzle_key: {"solved": bool, "active_cand": int, "candidates": [...]}}
        puzzle_states = {}

        for puzzle_idx, (row, src, candidates) in enumerate(
            zip(rows, src_parquets, results)
        ):
            n_processed += 1
            puzzle_passed = False
            cand_statuses = []
            puzzle_key = (row.puzzle_name1, row.puzzle_name2)
            initial_msgs = initial_messages[puzzle_idx]
            state = {"solved": False, "active_cand": 0, "candidates": []}

            for cand_idx, (thinking, response) in enumerate(candidates):
                try:
                    asp_code, _ = _try_extract_asp_block(thinking, response)
                except ValueError as e:
                    logger.info(
                        f"[{n_processed}] {row.puzzle_name1} cand={cand_idx}: "
                        f"no ASP block — {e}"
                    )
                    record = _make_initial_record(
                        row,
                        src,
                        args.prompt_template,
                        args.n_candidates,
                        cand_idx,
                        response=response,
                        asp_code="",
                        summary=None,
                        thinking=thinking,
                    )
                    writer.write(record)
                    cand_statuses.append("NO_BLOCK")
                    state["candidates"].append(
                        {
                            "asp_code": "",
                            "validation": None,
                            "thinking": thinking,
                            "response": response,
                            "initial_msgs": initial_msgs,
                            "history": [],
                        }
                    )
                    continue

                record, summary = _validate_and_write(
                    writer,
                    row,
                    src,
                    args.prompt_template,
                    args.n_candidates,
                    cand_idx,
                    response,
                    thinking,
                    asp_code,
                )

                state["candidates"].append(
                    {
                        "asp_code": asp_code,
                        "validation": summary,
                        "thinking": thinking,
                        "response": response,
                        "initial_msgs": initial_msgs,
                        "history": [],
                    }
                )

                if summary["passed"]:
                    puzzle_passed = True
                    cand_statuses.append(
                        f"PASS({summary['correct']}/{summary['total']})"
                    )
                else:
                    cand_statuses.append(
                        f"FAIL({summary['correct']}/{summary['total']})"
                    )

            if puzzle_passed:
                n_puzzles_passed += 1
                state["solved"] = True

            puzzle_states[puzzle_key] = state

            elapsed = time.perf_counter() - t0
            logger.info(
                f"[{n_processed}/{args.n}] {row.puzzle_name1}/{row.puzzle_name2}"
                f" sid={row.sid}  [{', '.join(cand_statuses)}]"
                f"  {elapsed:.0f}s elapsed"
            )

        # ── Phase 3: Refinement loop ────────────────────────────────────────
        if max_refinement <= 0:
            continue

        # Rebuild rows_lookup for refinement (maps puzzle_key -> row, src)
        rows_lookup = {}
        for row_idx, row in enumerate(rows):
            rows_lookup[(row.puzzle_name1, row.puzzle_name2)] = (
                row,
                src_parquets[row_idx],
            )

        for round_num in range(1, max_refinement + 1):
            batch_msgs = []
            batch_meta = []  # (puzzle_key, candidate_idx, feedback, asp_code, trigger, pair_idx)

            for puzzle_key, state in puzzle_states.items():
                if state["solved"]:
                    continue

                active_idx = state["active_cand"]
                if active_idx >= len(state["candidates"]):
                    continue

                cand = state["candidates"][active_idx]

                # Skip if this candidate already passed
                if cand["validation"] and cand["validation"]["passed"]:
                    state["solved"] = True
                    n_puzzles_passed += 1
                    logger.info(
                        f"  Puzzle {puzzle_key} solved by cand={active_idx} (already passed)"
                    )
                    continue

                # Skip NO_BLOCK candidates — can't refine without code
                if cand["validation"] is None:
                    state["active_cand"] += 1
                    logger.info(
                        f"  Puzzle {puzzle_key} cand={active_idx}: NO_BLOCK, "
                        f"advancing to cand={state['active_cand']}"
                    )
                    continue

                # Check if this candidate has been refined enough
                if len(cand["history"]) >= max_refinement:
                    state["active_cand"] += 1
                    logger.info(
                        f"  Puzzle {puzzle_key} cand={active_idx}: exhausted rounds, moving to cand={state['active_cand']}"
                    )
                    continue

                # Build feedback for this candidate
                validation = cand["validation"]
                if validation is None:
                    continue  # no ASP block extracted, can't refine

                trigger, pair_idx, details = categorize_first_failure(
                    validation["pairs"]
                )
                if trigger is None:
                    continue  # all passed — shouldn't happen but guard

                row, src = rows_lookup[puzzle_key]
                all_pairs = load_grid_pairs(row.puzzle_name1, row.puzzle_name2)
                input_grid, expected_grid = all_pairs[pair_idx]

                feedback = build_feedback_message(
                    asp_code=cand["asp_code"],
                    trigger=trigger,
                    pair_index=pair_idx,
                    details=details,
                    input_grid=input_grid,
                    expected_grid=expected_grid,
                    round_num=round_num,
                )

                # Build multi-turn conversation
                msgs = build_refinement_messages(
                    initial_messages=cand["initial_msgs"],
                    conversation_history=cand["history"],
                    response_text=cand["response"] or cand["thinking"],
                    feedback_message=feedback,
                )

                batch_msgs.append(msgs)
                batch_meta.append(
                    (
                        puzzle_key,
                        active_idx,
                        cand,
                        feedback,
                        trigger,
                        pair_idx,
                        (input_grid, expected_grid),
                    )
                )

            if not batch_msgs:
                break

            logger.info(
                f"Refinement round {round_num}: sending {len(batch_msgs)} fix prompts"
            )

            ref_results = engine.generate_batch(batch_msgs, n=1)

            # Process refinement results
            for meta, candidates in zip(batch_meta, ref_results):
                thinking, response = candidates[0]
                puzzle_key, cand_idx, cand, feedback, trigger, pair_idx, grids = meta
                input_grid, expected_grid = grids

                try:
                    asp_code, _ = _try_extract_asp_block(thinking, response)
                except ValueError as e:
                    logger.info(
                        f"  Refine r{round_num} {puzzle_key} cand={cand_idx}: no ASP block — {e}"
                    )
                    cand["history"].append((response or thinking, feedback))
                    continue

                # Validate refined program
                row, src = rows_lookup[puzzle_key]
                summary = validate_asp_program(
                    asp_code, row.puzzle_name1, row.puzzle_name2
                )

                # Compute grid diff for wrong_cells failures (pre-refinement)
                grid_diff = None
                if trigger == "wrong_cells" and details.get("actual_atoms"):
                    grid_diff = compute_grid_diff(
                        expected_grid, details["actual_atoms"]
                    )

                # Write refinement record
                ref_record = _make_refinement_record(
                    row,
                    src,
                    args.prompt_template,
                    args.n_candidates,
                    cand_idx,
                    response=response,
                    thinking=thinking,
                    asp_code=asp_code,
                    summary=summary,
                    round_num=round_num,
                    trigger=trigger,
                    pair_idx=pair_idx,
                    feedback_prompt=feedback,
                    grid_diff=grid_diff,
                )
                writer.write(ref_record)

                # Update candidate state
                cand["asp_code"] = asp_code
                cand["validation"] = summary
                cand["thinking"] = thinking
                cand["response"] = response
                cand["history"].append((response or thinking, feedback))

                if summary["passed"]:
                    puzzle_states[puzzle_key]["solved"] = True
                    n_puzzles_passed += 1
                    logger.info(
                        f"  Puzzle {puzzle_key} SOLVED at refinement round {round_num} "
                        f"by cand={cand_idx}"
                    )
                else:
                    logger.info(
                        f"  Refine r{round_num} {puzzle_key} cand={cand_idx}: "
                        f"FAIL({summary['correct']}/{summary['total']})"
                    )

    logger.info(
        f"Done: {n_puzzles_passed}/{n_processed} puzzles had at least one passing candidate."
        f" Results in {args.output_file}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch NVARC → ASP pipeline")
    parser.add_argument(
        "--n", type=int, default=100, help="Target number of unique puzzles to sample"
    )
    parser.add_argument(
        "--output-file",
        default="../outputs/batch_results.jsonl",
        help="Output JSONL file (appended to if it already exists)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=MAX_NUM_SEQS,
        help="Puzzles per vLLM batch (default: MAX_NUM_SEQS)",
    )
    parser.add_argument(
        "--n-candidates",
        type=int,
        default=N_CANDIDATES,
        help="Candidate completions per puzzle (vLLM `n`; default: N_CANDIDATES)",
    )
    parser.add_argument(
        "--max-refinement-attempts",
        type=int,
        default=MAX_REFINEMENT_ATTEMPTS,
        help="Max refinement rounds per candidate (0 = no refinement)",
    )
    parser.add_argument("--prompt-template", default=NVARC_ASP_PROMPT)
    parser.add_argument("--seed", default=DEFAULT_SEED, type=int)
    parser.add_argument("--host", default=VLLM_HOST)
    parser.add_argument("--port", default=VLLM_PORT, type=int)
    main(parser.parse_args())
