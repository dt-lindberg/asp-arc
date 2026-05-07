"""vLLM server-backed engine client for GPT-OSS-120B.

Talks to a `vllm serve` process via its OpenAI-compatible /v1 endpoint.
The server owns chat-template rendering (Harmony) and continuous batching.
"""

import asyncio
import time

from openai import AsyncOpenAI

from config.config import SEED, LOG_LEVEL
from config.config_llm import (
    MODEL_REPO_ID,
    VLLM_HOST,
    VLLM_PORT,
    REASONING_EFFORT,
    MAX_TOKENS,
    MAX_NUM_SEQS,
    TEMPERATURE,
    TOP_P,
    TOP_K,
    VLLM_REQUEST_TIMEOUT,
)
from utils.logger import setup_logging, get_logger

setup_logging(log_level=LOG_LEVEL)
logger = get_logger(__name__)


class VLLMEngine:
    def __init__(self, host=None, port=None, seed=None):
        host = host if host is not None else VLLM_HOST
        port = port if port is not None else VLLM_PORT
        self.seed = SEED if seed is None else seed

        self.client = AsyncOpenAI(
            base_url=f"http://{host}:{port}/v1",
            api_key="empty",
            timeout=VLLM_REQUEST_TIMEOUT,
        )
        self.sem = asyncio.Semaphore(MAX_NUM_SEQS)

        logger.debug(
            f"VLLMEngine client pointing at http://{host}:{port}/v1  "
            f"(seed={self.seed}, max_concurrent={MAX_NUM_SEQS})"
        )

    @staticmethod
    def _extract_message(msg_dict):
        reasoning = (
            msg_dict.get("reasoning_content")
            or msg_dict.get("reasoning")
            or ""
        )
        content = msg_dict.get("content", "")
        if isinstance(content, list):
            text = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        else:
            text = content or ""
        return reasoning, text

    async def _one_chat(self, messages, n=1):
        async with self.sem:
            resp = await self.client.chat.completions.create(
                model=MODEL_REPO_ID,
                messages=messages,
                n=n,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                top_p=TOP_P,
                seed=self.seed,
                extra_body={
                    "top_k": TOP_K,
                    "include_reasoning": True,
                    "reasoning_effort": REASONING_EFFORT,
                },
            )
            choices = resp.model_dump()["choices"]
            return [self._extract_message(c["message"]) for c in choices]

    async def generate_batch_async(self, messages_list, n=1):
        tasks = [self._one_chat(messages, n=n) for messages in messages_list]
        return await asyncio.gather(*tasks)

    def generate_batch(self, messages_list, n=1):
        """Generate responses for a batch of conversations.

        Args:
            messages_list: list of conversations, each a list of role/content dicts.
            n: number of candidate completions per prompt (vLLM `n` parameter).

        Returns:
            list (one entry per prompt) of lists of (thinking, response) tuples,
            length n. Thinking is "" when absent.
        """
        logger.debug(f"Generating batch of {len(messages_list)} prompts (n={n})...")
        t0 = time.perf_counter()

        results = asyncio.run(self.generate_batch_async(messages_list, n=n))

        t_gen = time.perf_counter() - t0
        total = sum(len(r) for r in results)
        logger.debug(f"Generated {total} responses across {len(results)} prompts in {t_gen:.2f}s")

        return results
