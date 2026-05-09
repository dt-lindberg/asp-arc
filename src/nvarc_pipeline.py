"""
NVARC → ASP pipeline.

Loads one NVARC puzzle, prompts GPT-OSS-120B to translate its Python solution
to ASP, then validates the generated program against all 30 input-output grid
pairs via Clingo.
"""

import argparse
import os

from config.config import LOG_LEVEL, SEED as DEFAULT_SEED
from config.config_llm import VLLM_HOST, VLLM_PORT
from config.config_nvarc import NVARC_ASP_PROMPT
from llm.vllm_engine import VLLMEngine
from utils.logger import setup_logging, get_logger
from utils.nvarc_data import load_output_row
from utils.nvarc_formatting import (
    build_prompt,
    extract_asp_block,
    extract_puzzle_xml,
    extract_python_code,
)
from utils.asp_validator import validate_asp_program

setup_logging(log_level=os.getenv("LOG_LEVEL", LOG_LEVEL))
logger = get_logger(__name__)

SEP = "=" * 70


def main(args):
    row = load_output_row(row_index=args.row_index)
    logger.info(
        f"Puzzle: mix={row.mix_name}  puzzle1={row.puzzle_name1}  "
        f"puzzle2={row.puzzle_name2}  sid={row.sid}  reasoning={row.reasoning_level}"
    )

    puzzle_xml = extract_puzzle_xml(row.prompt)
    python_code = extract_python_code(row.completion)

    with open(args.prompt_template, encoding="utf-8") as f:
        template = f.read()

    prompt = build_prompt(template, puzzle_xml, python_code)
    logger.info(f"Prompt ready ({len(prompt)} chars)")

    engine = VLLMEngine(host=args.host, port=args.port, seed=args.seed)
    [[(thinking, response)]] = engine.generate_batch(
        [[{"role": "user", "content": prompt}]]
    )

    print(SEP)
    print(f"PUZZLE  mix={row.mix_name}  puzzle1={row.puzzle_name1}  sid={row.sid}")
    print(SEP)
    print("PROMPT")
    print(SEP)
    print(prompt)
    print(SEP)
    print("THINKING")
    print(SEP)
    print(f"Thought for {len(thinking)} characters")
    print(f"First and last 200 characters:\n{thinking[:200]}\n---\n{thinking[-200:]}")
    print(SEP)
    print("RESPONSE")
    print(SEP)
    print(response or "(empty)")
    print(SEP)
    # Step 3: validate
    print("VALIDATION")
    print(SEP)

    try:
        asp_code = extract_asp_block(response)
        logger.info(f"ASP block extracted ({len(asp_code)} chars)")
    except ValueError as e:
        print(f"EXTRACTION FAILED: {e}")
        print(SEP)
        return

    summary = validate_asp_program(asp_code, row.puzzle_name1, row.puzzle_name2)

    result_tag = "PASS" if summary["passed"] else "FAIL"
    print(
        f"RESULT: {result_tag}  ({summary['correct']}/{summary['total']} pairs correct)"
    )

    for msg in summary["clingo_errors"]:
        print(f"  • {msg}")

    print(SEP)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NVARC → ASP translation pipeline")
    parser.add_argument(
        "--row-index",
        type=int,
        default=0,
        help="Row index within the first outputs/ parquet file",
    )
    parser.add_argument(
        "--prompt-template",
        default=NVARC_ASP_PROMPT,
        help="Path to the prompt template file",
    )
    parser.add_argument("--seed", default=DEFAULT_SEED, type=int)
    parser.add_argument("--host", default=VLLM_HOST)
    parser.add_argument("--port", default=VLLM_PORT, type=int)
    main(parser.parse_args())
