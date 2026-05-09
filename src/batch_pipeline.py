"""
Single-candidate NVARC → ASP batch pipeline with refinement loop.

Per puzzle: one initial generation, then up to MAX_REFINEMENT_ATTEMPTS
refinement turns on a multi-turn conversation thread fed by Clingo feedback.
The moment a generation passes Clingo, the puzzle is done.

This replaces the older multi-candidate pipeline (now under src/archived/).
The motivation is throughput: spending the budget on refinement of a single
candidate, only for puzzles that actually need it, beats spending it on
parallel candidates that mostly redo work the first one already got right.

Records are appended to JSONL after every generation (initial and refinement),
so progress survives a crash and the file alone is enough to reconstruct the
final state per puzzle.

* Edge case worth flagging
  - When the model's `max_tokens` budget is exhausted during reasoning, the
    visible `response` field is empty while `thinking` is huge. We NEVER feed
    `thinking` back as assistant content — that would push the next round's
    prompt past `max-model-len` and crash the request. The empty response is
    replayed as a "(no response)" placeholder; the thinking is preserved only
    in the JSONL record for that round.
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
    build_no_block_feedback_message,
    build_refinement_messages,
    categorize_first_failure,
    compute_grid_diff,
)

setup_logging(log_level=os.getenv("LOG_LEVEL", LOG_LEVEL))
logger = get_logger(__name__)


# Sentinel trigger for the "no asp block was extracted" refinement path.
# Distinct from the four Clingo-failure triggers in categorize_first_failure().
NO_BLOCK_TRIGGER = "no_block"


def _summary_for_record(summary):
    """JSONL-safe slice of a validation summary (drops the per-pair details)."""
    if summary is None:
        return None
    return {
        "passed": summary["passed"],
        "correct": summary["correct"],
        "total": summary["total"],
        "clingo_errors": summary["clingo_errors"],
    }


def _make_record(
    row,
    source_parquet,
    prompt_template,
    response,
    thinking,
    asp_code,
    summary,
    refinement_round,
    trigger=None,
    pair_index=None,
    feedback_prompt=None,
    grid_diff=None,
):
    """Build a JSONL record. Refinement-only fields are added when present."""
    record = {
        "puzzle_name1": row.puzzle_name1,
        "puzzle_name2": row.puzzle_name2,
        "sid": int(row.sid),
        "candidate_index": 0,
        "source_parquet": source_parquet,
        "prompt_template": prompt_template,
        "parameters": {
            "model": MODEL_REPO_ID,
            "reasoning_effort": REASONING_EFFORT,
            "temperature": TEMPERATURE,
            "top_k": TOP_K,
            "max_tokens": MAX_TOKENS,
            "n_candidates": 1,
        },
        "thinking": thinking,
        "response": response,
        "asp_code": asp_code,
        "validation": _summary_for_record(summary),
        "refinement_round": refinement_round,
    }
    if refinement_round > 0:
        record["trigger"] = trigger
        record["first_failing_pair_index"] = pair_index
        record["feedback_prompt"] = feedback_prompt
        record["grid_diff"] = grid_diff
    return record


def _try_extract_asp_block(response):
    """Extract a single ```asp block from response. Returns "" on failure."""
    if not response:
        return ""
    try:
        return extract_asp_block(response)
    except ValueError:
        return ""


def _build_round_feedback(state):
    """Build the user feedback for the next refinement turn.

    Returns (feedback_text, trigger, pair_index, grid_diff).

    * Three cases
      - state has no ASP block (NO_BLOCK)            → ambiguous-cause feedback
      - state's program failed Clingo validation     → category-specific feedback
      - state's program passed                       → caller should not call us
    """
    validation = state["validation"]

    # NO_BLOCK: response was missing or malformed; cause is ambiguous.
    if validation is None:
        return build_no_block_feedback_message(), NO_BLOCK_TRIGGER, -1, None

    trigger, pair_idx, details = categorize_first_failure(validation["pairs"])
    if trigger is None:
        # Should not happen: caller filters out solved puzzles.
        return None, None, -1, None

    # wrong_cells / wrong_count / unsat / syntax_error all need the failing pair grids.
    row = state["row"]
    all_pairs = load_grid_pairs(row.puzzle_name1, row.puzzle_name2)
    input_grid, expected_grid = all_pairs[pair_idx]

    grid_diff = None
    if trigger == "wrong_cells" and details.get("actual_atoms"):
        grid_diff = compute_grid_diff(expected_grid, details["actual_atoms"])

    feedback = build_feedback_message(
        asp_code=state["asp_code"],
        trigger=trigger,
        pair_index=pair_idx,
        details=details,
        input_grid=input_grid,
        expected_grid=expected_grid,
        round_num=state["round"] + 1,
    )
    return feedback, trigger, pair_idx, grid_diff


def _process_generation_result(state, thinking, response, row, prompt_template):
    """Validate a freshly-generated (thinking, response). Mutates state in place.

    Returns (asp_code, summary). asp_code is "" on NO_BLOCK; summary is None
    on NO_BLOCK and otherwise the full validate_asp_program() dict.
    """
    asp_code = _try_extract_asp_block(response)
    summary = (
        validate_asp_program(asp_code, row.puzzle_name1, row.puzzle_name2)
        if asp_code
        else None
    )
    state["asp_code"] = asp_code
    state["response"] = response
    state["thinking"] = thinking
    state["validation"] = summary
    if summary is not None and summary["passed"]:
        state["solved"] = True
    return asp_code, summary


def _phase_initial(
    engine, chunk, template, prompt_template_path, writer, t0, n_total, n_seen
):
    """Run the initial-generation phase for one chunk.

    Returns a dict puzzle_states keyed by (puzzle_name1, puzzle_name2). Skips
    puzzles whose extraction fails. Writes one initial record per puzzle.
    """
    rows, src_parquets, initial_messages = [], [], []
    for row, src in chunk:
        try:
            puzzle_xml = extract_puzzle_xml(row.prompt)
            python_code = extract_python_code(row.completion)
            prompt = build_prompt(template, puzzle_xml, python_code)
        except ValueError as e:
            logger.warning(f"Skipping {row.puzzle_name1}/{row.puzzle_name2}: {e}")
            continue
        rows.append(row)
        src_parquets.append(src)
        initial_messages.append([{"role": "user", "content": prompt}])

    if not initial_messages:
        return {}, n_seen

    results = engine.generate_batch(initial_messages, n=1)

    puzzle_states = {}
    for row, src, msgs, candidates in zip(
        rows, src_parquets, initial_messages, results
    ):
        n_seen += 1
        thinking, response = candidates[0]

        state = {
            "row": row,
            "src": src,
            "initial_msgs": msgs,
            "asp_code": "",
            "response": "",
            "thinking": "",
            "validation": None,
            "history": [],
            "round": 0,
            "solved": False,
        }
        _process_generation_result(state, thinking, response, row, prompt_template_path)

        record = _make_record(
            row=row,
            source_parquet=src,
            prompt_template=prompt_template_path,
            response=response,
            thinking=thinking,
            asp_code=state["asp_code"],
            summary=state["validation"],
            refinement_round=0,
        )
        writer.write(record)

        if state["asp_code"]:
            tag = (
                f"PASS({state['validation']['correct']}/{state['validation']['total']})"
                if state["solved"]
                else f"FAIL({state['validation']['correct']}/{state['validation']['total']})"
            )
        else:
            tag = "NO_BLOCK"

        elapsed = time.perf_counter() - t0
        logger.info(
            f"[{n_seen}/{n_total}] {row.puzzle_name1}/{row.puzzle_name2}"
            f" sid={row.sid}  initial={tag}  {elapsed:.0f}s elapsed"
        )

        puzzle_states[(row.puzzle_name1, row.puzzle_name2)] = state

    return puzzle_states, n_seen


def _phase_refinement(
    engine, puzzle_states, prompt_template_path, writer, max_refinement, t0
):
    """Run refinement rounds until everyone solves or budget exhausts.

    Each round batches all unsolved puzzles into a single generate_batch call.
    """
    if max_refinement <= 0:
        return

    for round_num in range(1, max_refinement + 1):
        batch_msgs = []
        batch_meta = []  # parallel list: (puzzle_key, feedback, trigger, pair_idx, grid_diff)

        for puzzle_key, state in puzzle_states.items():
            if state["solved"]:
                continue

            feedback, trigger, pair_idx, grid_diff = _build_round_feedback(state)
            if feedback is None:
                continue

            # Build the multi-turn messages.
            #   initial + (prev_resp, prev_feedback) pairs from history
            #   + (current state.response, new feedback)
            # Note: state.response is replayed as the latest assistant turn.
            # state.thinking is intentionally never replayed — see module docstring.
            msgs = build_refinement_messages(
                initial_messages=state["initial_msgs"],
                conversation_history=state["history"],
                response_text=state["response"],
                feedback_message=feedback,
            )
            batch_msgs.append(msgs)
            batch_meta.append((puzzle_key, feedback, trigger, pair_idx, grid_diff))

        if not batch_msgs:
            break

        logger.info(
            f"Refinement round {round_num}: sending {len(batch_msgs)} fix prompts"
        )

        ref_results = engine.generate_batch(batch_msgs, n=1)

        for (puzzle_key, feedback, trigger, pair_idx, grid_diff), candidates in zip(
            batch_meta, ref_results
        ):
            thinking, response = candidates[0]
            state = puzzle_states[puzzle_key]
            row = state["row"]
            src = state["src"]

            # Append (old response, feedback used this round) to history BEFORE
            # we overwrite state.response. This pairs each prior assistant turn
            # with the feedback that followed it, keeping the alternation clean
            # for build_refinement_messages on subsequent rounds.
            state["history"].append((state["response"], feedback))
            state["round"] = round_num

            _process_generation_result(
                state, thinking, response, row, prompt_template_path
            )

            record = _make_record(
                row=row,
                source_parquet=src,
                prompt_template=prompt_template_path,
                response=response,
                thinking=thinking,
                asp_code=state["asp_code"],
                summary=state["validation"],
                refinement_round=round_num,
                trigger=trigger,
                pair_index=pair_idx,
                feedback_prompt=feedback,
                grid_diff=grid_diff,
            )
            writer.write(record)

            elapsed = time.perf_counter() - t0
            if state["solved"]:
                logger.info(
                    f"  Refine r{round_num} {puzzle_key} SOLVED  {elapsed:.0f}s elapsed"
                )
            elif state["asp_code"]:
                v = state["validation"]
                logger.info(
                    f"  Refine r{round_num} {puzzle_key}: FAIL({v['correct']}/{v['total']})"
                    f"  trigger={trigger}  {elapsed:.0f}s elapsed"
                )
            else:
                logger.info(
                    f"  Refine r{round_num} {puzzle_key}: NO_BLOCK  {elapsed:.0f}s elapsed"
                )


def main(args):
    with open(args.prompt_template, encoding="utf-8") as f:
        template = f.read()

    writer = OutputWriter(args.output_file)
    engine = VLLMEngine(host=args.host, port=args.port, seed=args.seed)
    sampler = sample_puzzles(args.n)

    n_seen = 0
    n_solved_total = 0
    t0 = time.perf_counter()

    for chunk in itertools.batched(sampler, args.chunk_size):
        puzzle_states, n_seen = _phase_initial(
            engine, chunk, template, args.prompt_template, writer, t0, args.n, n_seen
        )

        _phase_refinement(
            engine,
            puzzle_states,
            args.prompt_template,
            writer,
            args.max_refinement_attempts,
            t0,
        )

        n_solved_chunk = sum(1 for s in puzzle_states.values() if s["solved"])
        n_solved_total += n_solved_chunk
        elapsed = time.perf_counter() - t0
        logger.info(
            f"Chunk done: {n_solved_chunk}/{len(puzzle_states)} solved."
            f"  Running total: {n_solved_total}/{n_seen}  ({elapsed:.0f}s elapsed)"
        )

    logger.info(
        f"Done: {n_solved_total}/{n_seen} puzzles solved (initial or refinement)."
        f"  Results in {args.output_file}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Single-candidate batch NVARC → ASP pipeline with refinement"
    )
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
        "--max-refinement-attempts",
        type=int,
        default=MAX_REFINEMENT_ATTEMPTS,
        help="Max refinement rounds per puzzle (0 = no refinement)",
    )
    parser.add_argument("--prompt-template", default=NVARC_ASP_PROMPT)
    parser.add_argument("--seed", default=DEFAULT_SEED, type=int)
    parser.add_argument("--host", default=VLLM_HOST)
    parser.add_argument("--port", default=VLLM_PORT, type=int)
    main(parser.parse_args())
