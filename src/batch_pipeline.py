"""
Batch NVARC → ASP pipeline.

Samples N unique puzzles across all outputs/ parquets, translates each to ASP
via GPT-OSS-120B, validates with Clingo, and writes results to a JSONL file.
Results are written after each chunk so intermediate progress survives a crash.
"""

import argparse
import itertools
import os
import time

from config.config import LOG_LEVEL, SEED as DEFAULT_SEED
from config.config_llm import (
    MAX_NUM_SEQS, MAX_TOKENS, MODEL_REPO_ID,
    N_CANDIDATES,
    REASONING_EFFORT, TEMPERATURE, TOP_K,
    VLLM_HOST, VLLM_PORT,
)
from config.config_nvarc import NVARC_ASP_PROMPT, NVARC_GRIDS_DIR
from llm.vllm_engine import VLLMEngine
from utils.asp_validator import validate_asp_program
from utils.logger import setup_logging, get_logger
from utils.nvarc_formatting import build_prompt, extract_asp_block, extract_puzzle_xml, extract_python_code
from utils.nvarc_sampler import sample_puzzles
from utils.output_writer import OutputWriter

setup_logging(log_level=os.getenv("LOG_LEVEL", LOG_LEVEL))
logger = get_logger(__name__)


def _make_record(row, source_parquet, prompt_template, n_candidates,
                 candidate_index, response, asp_code, summary, thinking):
    return {
        "puzzle_name1":    row.puzzle_name1,
        "puzzle_name2":    row.puzzle_name2,
        "sid":             int(row.sid),
        "candidate_index": candidate_index,
        "source_parquet":  source_parquet,
        "prompt_template": prompt_template,
        "parameters": {
            "model":            MODEL_REPO_ID,
            "reasoning_effort": REASONING_EFFORT,
            "temperature":      TEMPERATURE,
            "top_k":            TOP_K,
            "max_tokens":       MAX_TOKENS,
            "n_candidates":     n_candidates,
        },
        "thinking": thinking,
        "response": response,
        "asp_code": asp_code,
        "validation": None if summary is None else {
            "passed":        summary["passed"],
            "correct":       summary["correct"],
            "total":         summary["total"],
            "clingo_errors": summary["clingo_errors"],
        },
    }


def main(args):
    with open(args.prompt_template, encoding="utf-8") as f:
        template = f.read()

    writer = OutputWriter(args.output_file)
    engine = VLLMEngine(host=args.host, port=args.port, seed=args.seed)
    sampler = sample_puzzles(args.n, seed=args.seed)

    n_processed = 0          # puzzles processed
    n_puzzles_passed = 0     # puzzles with at least one passing candidate
    t0 = time.perf_counter()

    for chunk in itertools.batched(sampler, args.chunk_size):
        # Build prompts, silently skipping rows with malformed data
        rows, src_parquets, prompts = [], [], []
        for row, src in chunk:
            grid_path = os.path.join(
                NVARC_GRIDS_DIR, row.puzzle_name1,
                f"{row.puzzle_name1}_{row.puzzle_name2}.json",
            )
            # Pre-check: skip before spending LLM compute on a puzzle we can't validate.
            # The parquets and grids datasets were generated independently, so a small number
            # of (puzzle_name1, puzzle_name2) pairs exist in the parquets but have no
            # corresponding grid file on scratch.
            if not os.path.isfile(grid_path):
                logger.warning(f"Skipping {row.puzzle_name1}/{row.puzzle_name2}: grid file missing")
                continue
            try:
                puzzle_xml  = extract_puzzle_xml(row.prompt)
                python_code = extract_python_code(row.completion)
                prompt      = build_prompt(template, puzzle_xml, python_code)
                rows.append(row)
                src_parquets.append(src)
                prompts.append([{"role": "user", "content": prompt}])
            except ValueError as e:
                logger.warning(f"Skipping {row.puzzle_name1}/{row.puzzle_name2}: {e}")

        if not prompts:
            continue

        # All prompts in the chunk are sent concurrently; vLLM semaphore throttles internally.
        # Each entry in `results` is a list of N_CANDIDATES (thinking, response) tuples.
        results = engine.generate_batch(prompts, n=args.n_candidates)

        # Validate and write each candidate immediately as the chunk completes
        for row, src, candidates in zip(rows, src_parquets, results):
            n_processed += 1
            puzzle_passed = False
            cand_statuses = []

            for cand_idx, (thinking, response) in enumerate(candidates):
                try:
                    asp_code = extract_asp_block(response)
                except ValueError as e:
                    logger.info(
                        f"[{n_processed}] {row.puzzle_name1} cand={cand_idx}: "
                        f"no ASP block — {e}"
                    )
                    # Still record the failed extraction so the (puzzle, sid, cand_idx)
                    # key is preserved and the raw response/thinking is retained.
                    writer.write(_make_record(
                        row, src, args.prompt_template, args.n_candidates,
                        cand_idx, response=response, asp_code="",
                        summary=None, thinking=thinking,
                    ))
                    cand_statuses.append("NO_BLOCK")
                    continue

                summary = validate_asp_program(asp_code, row.puzzle_name1, row.puzzle_name2)
                writer.write(_make_record(
                    row, src, args.prompt_template, args.n_candidates,
                    cand_idx, response=response, asp_code=asp_code,
                    summary=summary, thinking=thinking,
                ))

                if summary["passed"]:
                    puzzle_passed = True
                    cand_statuses.append(f"PASS({summary['correct']}/{summary['total']})")
                else:
                    cand_statuses.append(f"FAIL({summary['correct']}/{summary['total']})")

            if puzzle_passed:
                n_puzzles_passed += 1
            elapsed = time.perf_counter() - t0
            logger.info(
                f"[{n_processed}/{args.n}] {row.puzzle_name1}/{row.puzzle_name2}"
                f" sid={row.sid}  [{', '.join(cand_statuses)}]"
                f"  {elapsed:.0f}s elapsed"
            )

    logger.info(
        f"Done: {n_puzzles_passed}/{n_processed} puzzles had at least one passing candidate."
        f" Results in {args.output_file}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch NVARC → ASP pipeline")
    parser.add_argument("--n", type=int, default=100,
                        help="Target number of unique puzzles to sample")
    parser.add_argument("--output-file", default="../outputs/batch_results.jsonl",
                        help="Output JSONL file (appended to if it already exists)")
    parser.add_argument("--chunk-size", type=int, default=MAX_NUM_SEQS,
                        help="Puzzles per vLLM batch (default: MAX_NUM_SEQS)")
    parser.add_argument("--n-candidates", type=int, default=N_CANDIDATES,
                        help="Candidate completions per puzzle (vLLM `n`; default: N_CANDIDATES)")
    parser.add_argument("--prompt-template", default=NVARC_ASP_PROMPT)
    parser.add_argument("--seed", default=DEFAULT_SEED, type=int)
    parser.add_argument("--host", default=VLLM_HOST)
    parser.add_argument("--port", default=VLLM_PORT, type=int)
    main(parser.parse_args())
