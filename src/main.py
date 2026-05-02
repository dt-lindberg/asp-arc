"""
Smoke-test orchestrator for the GPT-OSS-120B engine.

Loads the LLM, sends one chat with the prompt at prompts/smoke_test.txt
(or whatever --prompt-file points at), and prints the model's thinking +
response. No dataset, no Clingo, no refinement loop.

NVARC ingestion + Python->ASP translation pipeline are intentionally not
wired up yet — see the project plan.
"""

import argparse
import os

from config.config import LOG_LEVEL, SEED as DEFAULT_SEED
from config.config_llm import PROMPT_PATHS, VLLM_HOST, VLLM_PORT
from llm.vllm_engine import VLLMEngine
from utils.logger import setup_logging, get_logger

setup_logging(log_level=os.getenv("LOG_LEVEL", LOG_LEVEL))
logger = get_logger(__name__)


def main(args):
    with open(args.prompt_file, encoding="utf-8") as f:
        user_prompt = f.read().strip()

    logger.info(f"Prompt file: {args.prompt_file} ({len(user_prompt)} chars)")
    engine = VLLMEngine(host=args.host, port=args.port, seed=args.seed)

    messages = [{"role": "user", "content": user_prompt}]
    [(thinking, response)] = engine.generate_batch([messages])

    logger.info("=" * 60)
    logger.info("THINKING:")
    logger.info(thinking or "(empty)")
    logger.info("=" * 60)
    logger.info("RESPONSE:")
    logger.info(response or "(empty)")
    logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GPT-OSS-120B smoke test")
    parser.add_argument(
        "--prompt-file",
        default=PROMPT_PATHS["smoke_test"],
        help="Path to a text file whose contents are sent as the user message.",
    )
    parser.add_argument(
        "--seed", default=DEFAULT_SEED, type=int, help="Seed for vLLM sampling"
    )
    parser.add_argument("--host", default=VLLM_HOST, help="vLLM server host")
    parser.add_argument("--port", default=VLLM_PORT, type=int, help="vLLM server port")
    args = parser.parse_args()
    main(args)
