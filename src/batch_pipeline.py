"""
Single-candidate NVARC → ASP batch pipeline with refinement.

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

* Concurrency model
  - A bounded worker pool of MAX_CONCURRENT_PUZZLES workers pulls puzzles
    lazily from the sampler via an asyncio.Queue. At any moment at most
    that many puzzles are alive in RAM, each running its own
    gen→validate→refine coroutine.
  - vLLM's continuous-batching scheduler runs --max-num-seqs requests in
    parallel and queues the rest in its waiting queue. As running requests
    finish, the scheduler admits the next pending one in the same step.
  - Refinement requests for a failing puzzle are submitted from inside that
    puzzle's coroutine, so they enter vLLM's queue alongside any initial
    requests still pending for other puzzles.
  - Net effect: ~MAX_CONCURRENT_PUZZLES requests in vLLM's pipeline at all
    times, GPU stays saturated, and the client process never grows past the
    state of MAX_CONCURRENT_PUZZLES live puzzles.

* Edge case worth flagging
  - When the model's `max_tokens` budget is exhausted during reasoning, the
    visible `response` field is empty while `thinking` is huge. We NEVER feed
    `thinking` back as assistant content — that would push the next round's
    prompt past `max-model-len` and crash the request. The empty response is
    replayed as a "(no response)" placeholder; the thinking is preserved only
    in the JSONL record for that round.
"""

import argparse
import asyncio
import os
import time

from config.config import (
    LOG_LEVEL,
    MAX_CONCURRENT_PUZZLES,
    MAX_REFINEMENT_ATTEMPTS,
    SEED as DEFAULT_SEED,
)
from config.config_llm import (
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


def _build_round_feedback(row, asp_code, summary, round_num):
    """Build the user feedback for the next refinement turn.

    Returns (feedback_text, trigger, pair_index, grid_diff), or None if all
    pairs already passed (caller should not invoke us in that case but we
    guard anyway).

    * Three cases
      - summary is None (NO_BLOCK)                  → ambiguous-cause feedback
      - summary failed Clingo validation             → category-specific feedback
      - summary passed                               → returns None
    """
    if summary is None:
        return build_no_block_feedback_message(), NO_BLOCK_TRIGGER, -1, None

    trigger, pair_idx, details = categorize_first_failure(summary["pairs"])
    if trigger is None:
        return None

    all_pairs = load_grid_pairs(row.puzzle_name1, row.puzzle_name2)
    input_grid, expected_grid = all_pairs[pair_idx]

    grid_diff = None
    if trigger == "wrong_cells" and details.get("actual_atoms"):
        grid_diff = compute_grid_diff(expected_grid, details["actual_atoms"])

    feedback = build_feedback_message(
        asp_code=asp_code,
        trigger=trigger,
        pair_index=pair_idx,
        details=details,
        input_grid=input_grid,
        expected_grid=expected_grid,
        round_num=round_num,
    )
    return feedback, trigger, pair_idx, grid_diff


async def handle_puzzle(
    engine,
    row,
    src,
    template,
    prompt_template_path,
    writer,
    max_refinement,
    t0,
    n_requested,
    counter,
):
    """Run one puzzle through gen → validate → (refine → gen → validate)* end-to-end.

    Each call is an independent coroutine. vLLM's scheduler queues the
    underlying chat requests across all concurrent puzzles, so concurrency
    here equals --max-num-seqs (running) plus whatever vLLM is willing to
    keep waiting.

    n_requested is the user-supplied --n; used as a soft denominator in
    progress logs. The actual yielded count can be lower if the sampler runs
    out of unsolved puzzles before reaching n_requested.

    * History bookkeeping
      - history holds (prev_response, prev_feedback) pairs from earlier
        rounds. Each pair represents an assistant turn followed by the user
        feedback that triggered the next round.
      - last_response is the most recent assistant response, replayed as
        response_text in the next refinement build.
      - thinking is intentionally never replayed — see module docstring.
    """
    p1, p2 = row.puzzle_name1, row.puzzle_name2

    try:
        puzzle_xml = extract_puzzle_xml(row.prompt)
        python_code = extract_python_code(row.completion)
        prompt = build_prompt(template, puzzle_xml, python_code)
    except ValueError as e:
        logger.warning(f"Skipping {p1}/{p2}: {e}")
        counter["skipped"] += 1
        return

    initial_msgs = [{"role": "user", "content": prompt}]
    history = []
    last_response = ""
    asp_code = ""
    summary = None
    feedback = None
    trigger = None
    pair_idx = None
    grid_diff = None

    for round_num in range(max_refinement + 1):
        if round_num == 0:
            msgs = initial_msgs
            trigger = pair_idx = grid_diff = feedback = None
        else:
            fb = _build_round_feedback(row, asp_code, summary, round_num)
            if fb is None:
                # All pairs already passed — caller shouldn't reach here.
                break
            feedback, trigger, pair_idx, grid_diff = fb
            msgs = build_refinement_messages(
                initial_messages=initial_msgs,
                conversation_history=history,
                response_text=last_response,
                feedback_message=feedback,
            )

        try:
            thinking, response = await engine.chat_async(msgs)
        except Exception as e:
            logger.error(f"{p1}/{p2} round {round_num}: vLLM error — {e}")
            counter["errored"] += 1
            return

        asp_code = _try_extract_asp_block(response)
        summary = (
            await asyncio.to_thread(validate_asp_program, asp_code, p1, p2)
            if asp_code
            else None
        )

        record = _make_record(
            row=row,
            source_parquet=src,
            prompt_template=prompt_template_path,
            response=response,
            thinking=thinking,
            asp_code=asp_code,
            summary=summary,
            refinement_round=round_num,
            trigger=trigger,
            pair_index=pair_idx,
            feedback_prompt=feedback,
            grid_diff=grid_diff,
        )
        await asyncio.to_thread(writer.write, record)

        elapsed = time.perf_counter() - t0
        phase = "initial" if round_num == 0 else f"refine r{round_num}"

        if summary and summary["passed"]:
            counter["solved"] += 1
            counter["done"] += 1
            logger.info(
                f"[{counter['done']}/~{n_requested}] {p1}/{p2} sid={row.sid}"
                f"  SOLVED at {phase}  {elapsed:.0f}s"
            )
            return

        if asp_code:
            logger.info(
                f"  {p1}/{p2} {phase}: FAIL({summary['correct']}/{summary['total']})"
                f"  {elapsed:.0f}s"
            )
        else:
            logger.info(f"  {p1}/{p2} {phase}: NO_BLOCK  {elapsed:.0f}s")

        # Append (response that was just used as response_text, feedback that
        # triggered this round) BEFORE updating last_response. Keeps the
        # alternation clean for the next refinement turn.
        if round_num >= 1:
            history.append((last_response, feedback))
        last_response = response

    counter["done"] += 1
    elapsed = time.perf_counter() - t0
    logger.info(
        f"[{counter['done']}/~{n_requested}] {p1}/{p2} sid={row.sid}"
        f"  UNSOLVED after {max_refinement} refinement(s)  {elapsed:.0f}s"
    )


async def main(args):
    with open(args.prompt_template, encoding="utf-8") as f:
        template = f.read()

    writer = OutputWriter(args.output_file)
    engine = VLLMEngine(host=args.host, port=args.port, seed=args.seed)

    counter = {"done": 0, "solved": 0, "skipped": 0, "errored": 0}
    t0 = time.perf_counter()

    # Bounded worker pool. The sampler is iterated lazily on the producer
    # side, and the queue's small maxsize means a row only enters RAM when
    # a worker is ready to take it. At any moment at most n_workers puzzles
    # are alive — exactly the cap we want.
    n_workers = min(args.max_concurrent_puzzles, args.n)
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)

    async def worker():
        while True:
            item = await queue.get()
            try:
                if item is None:
                    return
                row, src = item
                await handle_puzzle(
                    engine,
                    row,
                    src,
                    template,
                    args.prompt_template,
                    writer,
                    args.max_refinement_attempts,
                    t0,
                    args.n,
                    counter,
                )
            finally:
                queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(n_workers)]
    logger.info(
        f"Started {n_workers} workers; streaming up to {args.n} puzzles "
        f"from the sampler."
    )

    # Producer: feed rows into the queue. queue.put() blocks when all
    # workers are busy, which is the backpressure that keeps RAM bounded.
    # The sampler reads parquets synchronously, so wrap iteration in a
    # thread to avoid stalling the event loop on disk I/O between rows.
    sampler_iter = iter(sample_puzzles(args.n))
    while True:
        item = await asyncio.to_thread(next, sampler_iter, None)
        if item is None:
            break
        await queue.put(item)

    # Poison-pill each worker so they exit cleanly.
    for _ in range(n_workers):
        await queue.put(None)

    await asyncio.gather(*workers)

    elapsed = time.perf_counter() - t0
    unsolved = counter["done"] - counter["solved"]
    logger.info(
        f"Done in {elapsed:.0f}s.  "
        f"solved={counter['solved']}/{counter['done']}, unsolved={unsolved}, "
        f"skipped={counter['skipped']}, errored={counter['errored']}.  "
        f"Results in {args.output_file}"
    )
    logger.info(
        f"Peak in-flight requests (client side): {engine._max_in_flight_seen}"
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
        "--max-refinement-attempts",
        type=int,
        default=MAX_REFINEMENT_ATTEMPTS,
        help="Max refinement rounds per puzzle (0 = no refinement)",
    )
    parser.add_argument(
        "--max-concurrent-puzzles",
        type=int,
        default=MAX_CONCURRENT_PUZZLES,
        help="Worker-pool size: max puzzles alive in RAM at any moment",
    )
    parser.add_argument("--prompt-template", default=NVARC_ASP_PROMPT)
    parser.add_argument("--seed", default=DEFAULT_SEED, type=int)
    parser.add_argument("--host", default=VLLM_HOST)
    parser.add_argument("--port", default=VLLM_PORT, type=int)
    asyncio.run(main(parser.parse_args()))
