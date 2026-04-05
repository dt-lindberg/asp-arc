"""vLLM engine for batched local inference with Nemotron-Cascade-2-30B-A3B (NVFP4).

Two engine classes:
* NemotronEngine     — synchronous, uses llm.chat(); used for all single-batch calls
  and the multi-turn syntax agent.
* AsyncNemotronEngine — asynchronous, uses AsyncLLMEngine.generate(); each
  generate_one() coroutine yields control so concurrent callers are batched
  automatically by vLLM's scheduler. Used for per-puzzle async refinement.

Thinking mode is enabled by default.
"""

import os
import re
import time
import uuid

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
        response = text[end_idx + len("</think>") :].strip()
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
        logger.info(
            f"Generating batch of {len(messages_list)} prompts via llm.chat()..."
        )
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


class AsyncNemotronEngine:
    """Async vLLM engine for per-puzzle concurrent refinement.

    Each call to generate_one() is a coroutine. When multiple coroutines
    concurrently await generate_one(), vLLM's internal scheduler batches all
    pending requests together, keeping the GPU saturated during the refinement
    loop without any explicit batching logic in the caller.

    * Uses AsyncLLMEngine (not LLM) — no .chat() API, so the chat template is
      applied manually via the HuggingFace tokenizer before each request.
    * Shares the same model weights as a synchronous NemotronEngine would; only
      one engine type should be instantiated at a time in a given process.
    """

    def __init__(self, max_model_len=MAX_MODEL_LEN, temperature=TEMPERATURE):
        from vllm.engine.async_llm_engine import AsyncLLMEngine
        from vllm import AsyncEngineArgs, SamplingParams
        from transformers import AutoTokenizer

        logger.info(f"Loading async engine: {MODEL_PATH}")
        t0 = time.perf_counter()

        engine_args = AsyncEngineArgs(
            model=MODEL_PATH,
            trust_remote_code=True,
            mamba_ssm_cache_dtype="float32",
            kv_cache_dtype="fp8",
            max_model_len=max_model_len,
            tensor_parallel_size=1,
            seed=SEED,
        )
        self.engine = AsyncLLMEngine.from_engine_args(engine_args)

        # Load tokenizer separately — AsyncLLMEngine has no .chat() shortcut.
        self.tokenizer = AutoTokenizer.from_pretrained(
            MODEL_PATH, trust_remote_code=True
        )
        self.sampling_params = SamplingParams(
            temperature=temperature,
            top_p=TOP_P,
            top_k=TOP_K,
            # vLLM clamps to remaining context; MAX_MODEL_LEN is the effective ceiling.
            max_tokens=max_model_len,
        )
        logger.info(f"Async engine loaded in {time.perf_counter() - t0:.2f}s")

    async def generate_one(self, messages):
        """Generate a single response; concurrent callers are batched by vLLM.

        Args:
            messages: list of {"role": str, "content": str} dicts (full conversation).

        Returns:
            (thinking, response) tuple, same contract as NemotronEngine.generate_batch.
        """
        prompt = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        request_id = str(uuid.uuid4())

        # Iterate through the stream; only the final output is used.
        final = None
        async for output in self.engine.generate(
            prompt, self.sampling_params, request_id
        ):
            final = output

        raw = final.outputs[0].text
        if "</think>" not in raw:
            logger.warning(
                f"[async] Output has no </think> tag — thinking trace may be missing. "
                f"Raw (first 200 chars): {repr(raw[:200])}"
            )
        return _split_thinking(raw)
