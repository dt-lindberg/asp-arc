"""vLLM engine for batched local inference with Nemotron-Cascade-2-30B-A3B (NVFP4).

Uses llm.chat() (not llm.generate()) as required by Nemotron's chat template.
Thinking mode is enabled by default.
"""

import os
import re
import time

from logger import setup_logging, get_logger

setup_logging(log_level=os.getenv("LOG_LEVEL", "debug"))
logger = get_logger(__name__)

NEMOTRON_MODEL_PATH = "chankhavu/Nemotron-Cascade-2-30B-A3B-NVFP4"
# MAX_TOKENS = 8192           # thinking (≤REASONING_BUDGET) + answer
MAX_MODEL_LEN = 262_144       # total context
# REASONING_BUDGET = 4096     # max thinking tokens; the model is trained to follow
                            # the {thinking token budget: N} annotation in the prompt
                            # and stop reasoning after N tokens to write the answer.
                            # Previously used wrong kwarg name 'thinking_budget'.
TEMPERATURE = 1.0
TOP_P = 0.95
TOP_K = 20


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
    def __init__(
        self,
        # max_tokens=MAX_TOKENS,
        max_model_len=MAX_MODEL_LEN,
        temperature=TEMPERATURE,
    ):
        from vllm import LLM, SamplingParams

        logger.info(f"Loading Nemotron model: {NEMOTRON_MODEL_PATH}")
        t0 = time.perf_counter()
        self.llm = LLM(
            model=NEMOTRON_MODEL_PATH,
            trust_remote_code=True,
            mamba_ssm_cache_dtype="float32",
            kv_cache_dtype="fp8",
            max_model_len=max_model_len,
            tensor_parallel_size=1,
        )
        logger.info(f"Model loaded in {time.perf_counter() - t0:.2f}s")

        self.sampling_params = SamplingParams(
            temperature=temperature,
            top_p=TOP_P,
            top_k=TOP_K,
            max_tokens=max_tokens,
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

        # Pass thinking_budget explicitly to prevent the chat template from
        # filling remaining context with budget tokens (which causes token overflow).
        outputs = self.llm.chat(
            messages=messages_list,
            sampling_params=self.sampling_params,
            # chat_template_kwargs={"reasoning_budget": REASONING_BUDGET},
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
