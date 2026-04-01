"""vLLM engine for batched local inference with Nemotron-Cascade-2-30B-A3B (NVFP4).

Uses llm.chat() (not llm.generate()) as required by Nemotron's chat template.
Thinking mode is enabled by default.
"""

import os
import re
import time

from logger import setup_logging, get_logger
from config import MODEL_PATH, SEED, MAX_MODEL_LEN, TEMPERATURE, TOP_P, TOP_K

setup_logging(log_level=os.getenv("LOG_LEVEL", "debug"))
logger = get_logger(__name__)


def _split_thinking(text):
    """Split raw output into (thinking, response).

    * thinking: content inside the first <think>...</think> block, or "" if absent.
    * response: remaining text after stripping the thinking block.
    """
    thinking_re = re.compile(r"<think>(.*?)</think>", re.DOTALL)
    match = thinking_re.search(text)
    if match:
        thinking = match.group(1).strip()
        response = thinking_re.sub("", text).strip()
        return thinking, response

    # <think> was in the prompt; output begins with thinking content directly
    end_idx = text.find("</think>")
    if end_idx != -1:
        thinking = text[:end_idx].strip()
        response = text[end_idx + len("</think>"):].strip()
        return thinking, response

    return "", text.strip()


class NemotronEngine:
    def __init__(self, max_model_len=MAX_MODEL_LEN, temperature=TEMPERATURE):
        from vllm import LLM, SamplingParams

        logger.info(f"Loading model: {MODEL_PATH}")
        t0 = time.perf_counter()
        self.llm = LLM(
            model=MODEL_PATH,
            trust_remote_code=True,
            mamba_ssm_cache_dtype="float32",
            kv_cache_dtype="fp8",
            max_model_len=max_model_len,
            tensor_parallel_size=1,
            seed=SEED,
        )
        logger.info(f"Model loaded in {time.perf_counter() - t0:.2f}s")

        self.sampling_params = SamplingParams(
            temperature=temperature,
            top_p=TOP_P,
            top_k=TOP_K,
            max_tokens=max_model_len,  # vLLM clamps to remaining context; MAX_MODEL_LEN is the effective ceiling
        )

    def generate_batch(self, messages_list):
        """Generate responses for a batch of conversations.

        Args:
            messages_list: list of conversations, each a list of role/content dicts.

        Returns:
            list of (thinking, response) tuples where thinking is "" when absent.
        """
        logger.info(f"Generating batch of {len(messages_list)} prompts via llm.chat()...")
        t0 = time.perf_counter()

        outputs = self.llm.chat(
            messages=messages_list,
            sampling_params=self.sampling_params,
        )
        t_gen = time.perf_counter() - t0

        n_tokens = sum(len(o.outputs[0].token_ids) for o in outputs)
        logger.info(
            f"Generated {n_tokens} tokens in {t_gen:.2f}s ({n_tokens / t_gen:.2f} tok/s)"
        )

        results = []
        for i, o in enumerate(outputs):
            raw = o.outputs[0].text
            if "</think>" not in raw:
                logger.warning(
                    f"Output {i} has no </think> tag — thinking trace may be missing. "
                    f"Raw output (first 200 chars): {repr(raw[:200])}"
                )
            results.append(_split_thinking(raw))

        return results
