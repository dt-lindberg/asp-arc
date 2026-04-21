"""vLLM engine for batched local inference"""

import time

from huggingface_hub import snapshot_download

from logger import setup_logging, get_logger
from agent.utils import split_thinking
from config.config import SEED, LOG_LEVEL
from config.config_agent import (
    MODEL_REPO_ID,
    THINKING,
    LANGUAGE_MODEL_ONLY,
    MAX_TOKENS,
    MAX_MODEL_LEN,
    MAX_NUM_BATCHED_TOKENS,
    MAX_NUM_SEQS,
    TEMPERATURE,
    GPU_MEMORY_UTILIZATION,
    TOP_P,
    TOP_K,
    MIN_P,
    PRESENCE_PENALTY,
    REPETITION_PENALTY,
)

setup_logging(log_level=LOG_LEVEL)
logger = get_logger(__name__)


class VLLMEngine:
    def __init__(self):
        """
        Initialize the vLLM engine and sampling parameters from config_agent.

        LLM parameters:
        * max_model_len:            max input+output tokens per sequence (not entire batch), throws error if exceeded
        * max_num_seqs:             max number of requests processed simultaneously
        * max_num_batched_tokens:   max input tokens to encode in batch (higher values demand more memory)
        * gpu_memory_utilization:   fraction of GPU VRAM max usage

        Sampling parameters:
        * max_tokens:   max output tokens per sequence (= think budget + response budget)
        * temperature:  sampling temperature; 0 = greedy
        * top_p:        nucleus sampling probability mass cutoff
        * top_k:        vocab truncation to top-k tokens before sampling
        * min_p:        minimum token probability relative to the top token
        """
        from vllm import LLM, SamplingParams

        logger.debug(f"Resolving snapshot for {MODEL_REPO_ID}")
        model_path = snapshot_download(
            repo_id=MODEL_REPO_ID,
            allow_patterns=["*.safetensors", "*.json", "*.txt", "tokenizer*", "*.py"],
        )
        logger.info(f"Loading model from {model_path}")
        t0 = time.perf_counter()

        # Define LLM parameters, add parameters as needed
        llm_kwargs = dict(
            model=model_path,
            max_model_len=MAX_MODEL_LEN,
            max_num_seqs=MAX_NUM_SEQS,
            max_num_batched_tokens=MAX_NUM_BATCHED_TOKENS,
            gpu_memory_utilization=GPU_MEMORY_UTILIZATION,
            seed=SEED,
        )
        # Add this for Qwen3.6 to skip vision part
        if LANGUAGE_MODEL_ONLY:
            llm_kwargs["language_model_only"] = True

        self.llm = LLM(**llm_kwargs)
        logger.info(f"Model loaded in {time.perf_counter() - t0:.2f}s")

        self.tokenizer = self.llm.get_tokenizer()
        # Build stop-token set: include the tokenizer's EOS plus <|im_end|>
        # explicitly.  With some GGUF checkpoints vLLM does not automatically
        # stop at <|im_end|>, causing the model to continue past its natural
        # end-of-turn and re-emit role markers ("assistant") in the output.
        stop_token_ids: list[int] = []
        eos_id = self.tokenizer.eos_token_id
        if eos_id is not None:
            stop_token_ids.append(eos_id)
        try:
            im_end_id = self.tokenizer.convert_tokens_to_ids("<|im_end|>")
            if im_end_id is not None and im_end_id not in stop_token_ids:
                stop_token_ids.append(im_end_id)
                logger.debug(
                    f"Added <|im_end|> (id={im_end_id}) as explicit stop token"
                )
        except Exception as e:
            logger.warning(f"Failed to add stop tokens, error={e}")
        logger.debug(f"Stop token ids: {stop_token_ids}")

        self.sampling_params = SamplingParams(
            seed=SEED,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            top_p=TOP_P,
            top_k=TOP_K,
            min_p=MIN_P,
            presence_penalty=PRESENCE_PENALTY,
            repetition_penalty=REPETITION_PENALTY,
            stop_token_ids=stop_token_ids if stop_token_ids else None,
        )

    def _apply_template(self, messages):
        """Apply chat template, enabling thinking mode for Qwen3."""
        formatted = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=THINKING,
        )
        return formatted

    def generate_batch(self, messages_list):
        """Generate responses for a batch of conversations.

        Args:
            messages_list: list of conversations, each a list of role/content dicts.

        Returns:
            list of (thinking, response) tuples where thinking is "" when absent.
        """
        formatted = [self._apply_template(msgs) for msgs in messages_list]

        logger.debug(f"Generating batch of {len(formatted)} prompts...")
        t0 = time.perf_counter()
        outputs = self.llm.generate(formatted, self.sampling_params)
        t_gen = time.perf_counter() - t0

        # Counts all response tokens; both thinking and output
        n_tokens = sum(len(o.outputs[0].token_ids) for o in outputs)
        logger.debug(
            f"Generated {n_tokens} tokens in {t_gen:.2f}s ({n_tokens / t_gen:.2f} tok/s)"
        )

        # Each response becomes tuple of (thinking, output)
        return [split_thinking(o.outputs[0].text) for o in outputs]
