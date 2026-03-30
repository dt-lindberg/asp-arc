"""LLM + Clingo pipeline for ARC-ASP.

Adapted from asp-gen-refinements/pipeline.py. Key differences:
- Prompts receive formatted ARC training examples, not logic puzzle text.
- gen_answer_set() is unchanged (Clingo runner).
- Caching is keyed on the full prompt string.
- No pandas/Excel output; results are saved as JSON by main.py.
"""

import json
import os
import threading

from logger import setup_logging, get_logger
from config import (
    DEFAULT_ENGINE,
    TEMPERATURE,
    MAX_TOKENS,
    CLINGO_MAX_MODELS,
    CLINGO_TIMEOUT,
    PROMPT_PATHS,
)

setup_logging(log_level=os.getenv("LOG_LEVEL", "debug"))
logger = get_logger(__name__)


class Context:
    """Clingo Python context (kept for compatibility; ARC programs rarely use it)."""

    pass


class Pipeline:
    def __init__(self, args=None):
        self.engine = DEFAULT_ENGINE
        self.temperature = TEMPERATURE
        self.max_tokens = MAX_TOKENS
        self.path_prompt = dict(PROMPT_PATHS)
        self.prompt = {}
        self.path_cache = {}
        self.cache = {}
        self._vllm_engine = None

        if args:
            for k, v in args.items():
                setattr(self, k, v)

        os.makedirs("caches", exist_ok=True)
        os.makedirs("outputs", exist_ok=True)

    # ------------------------------------------------------------------
    # Engine
    # ------------------------------------------------------------------

    def _get_engine(self):
        if self._vllm_engine is None:
            from vllm_engine import VLLMEngine

            self._vllm_engine = VLLMEngine(
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
        return self._vllm_engine

    # ------------------------------------------------------------------
    # Prompts & cache
    # ------------------------------------------------------------------

    def load_prompts(self):
        for kind, path in self.path_prompt.items():
            with open(path, encoding="utf-8") as f:
                self.prompt[kind] = f.read().strip()
        logger.debug(f"Loaded {len(self.prompt)} prompt templates")

    def _cache_path(self, kind):
        prefix = f"vllm_{self.engine}_"
        return f"caches/{prefix}{kind}.json"

    def load_cache(self):
        for kind in self.path_prompt:
            path = self._cache_path(kind)
            if os.path.isfile(path):
                with open(path) as f:
                    self.cache[kind] = json.load(f)
            else:
                self.cache[kind] = {}
        logger.debug("Cache loaded")

    def save_cache(self, kind):
        path = self._cache_path(kind)
        with open(path, "w") as f:
            json.dump(self.cache[kind], f)

    @staticmethod
    def _cache_response(entry):
        return entry["response"] if isinstance(entry, dict) else entry

    @staticmethod
    def _cache_thinking(entry):
        return entry.get("thinking", "") if isinstance(entry, dict) else ""

    # ------------------------------------------------------------------
    # Batch generation — template-based
    # ------------------------------------------------------------------

    def gen_response_batch(self, kind, replaces):
        """Generate responses for a batch of puzzles using a prompt template.

        Args:
            kind: prompt key (e.g. "constants", "predicates").
            replaces: list of {placeholder: value} dicts, one per puzzle.

        Returns:
            list of (thinking, response) tuples.
        """
        prompts = []
        for replace in replaces:
            p = self.prompt[kind]
            for k, v in replace.items():
                p = p.replace(k, v)
            prompts.append(p)

        return self._gen_from_prompts(kind, prompts)

    # ------------------------------------------------------------------
    # Batch generation — raw prompt strings (used by refinement loop)
    # ------------------------------------------------------------------

    def gen_response_raw_batch(self, kind, prompts):
        """Generate responses for pre-built prompt strings."""
        return self._gen_from_prompts(kind, prompts)

    # ------------------------------------------------------------------
    # Internal: cache check + vLLM call
    # ------------------------------------------------------------------

    def _gen_from_prompts(self, kind, prompts):
        """Check cache, call vLLM for misses, return (thinking, response) list."""
        results = [None] * len(prompts)
        miss_indices = []
        miss_messages = []

        for i, prompt in enumerate(prompts):
            if prompt in self.cache.get(kind, {}):
                entry = self.cache[kind][prompt]
                results[i] = (self._cache_thinking(entry), self._cache_response(entry))
            else:
                miss_indices.append(i)
                miss_messages.append([{"role": "user", "content": prompt}])

        if miss_messages:
            generated = self._get_engine().generate_batch(miss_messages)
            if kind not in self.cache:
                self.cache[kind] = {}
            for idx, (thinking, resp) in zip(miss_indices, generated):
                self.cache[kind][prompts[idx]] = {"response": resp, "thinking": thinking}
                results[idx] = (thinking, resp)
            self.save_cache(kind)

        return results

    # ------------------------------------------------------------------
    # Clingo
    # ------------------------------------------------------------------

    def gen_answer_set(self, program):
        """Run Clingo on a program string.

        Returns:
            (None, list_of_answer_sets)    on success.
            (RuntimeError, error_messages) on parse/ground error or timeout.
        """
        from clingo.control import Control

        clingo_messages = []

        def _clingo_logger(code, message):
            clingo_messages.append((code, message))

        ctl = Control(
            [str(CLINGO_MAX_MODELS), "--warn=none", "--opt-mode=optN", "-t", "4"],
            logger=_clingo_logger,
        )
        models = []

        try:
            program_clean = program.encode("ascii", errors="replace").decode("ascii")
            logger.debug(f"Adding program to Clingo ({len(program_clean)} chars)")
            ctl.add("base", [], program_clean)
        except RuntimeError as e:
            logger.info(f"Clingo parse error: {e} ({len(clingo_messages)} messages)")
            return RuntimeError, clingo_messages
        except Exception as e:
            logger.error(f"Clingo add() failed: {e}")
            return RuntimeError, []

        # Ground in a daemon thread to enforce a timeout
        ground_exc = [None]
        ground_done = threading.Event()

        def _do_ground():
            try:
                ctl.ground([("base", [])], context=Context())
            except Exception as e:
                ground_exc[0] = e
            finally:
                ground_done.set()

        threading.Thread(target=_do_ground, daemon=True).start()
        if not ground_done.wait(CLINGO_TIMEOUT):
            logger.warning(f"Clingo ground() timed out after {CLINGO_TIMEOUT}s")
            return RuntimeError, []

        if ground_exc[0] is not None:
            e = ground_exc[0]
            if isinstance(e, RuntimeError):
                logger.info(f"Clingo grounding error: {e}")
                return RuntimeError, clingo_messages
            logger.error(f"Clingo grounding failed: {e}")
            return RuntimeError, []

        on_model = lambda model: models.append(model.symbols(atoms=True))

        with ctl.solve(on_model=on_model, async_=True) as handle:
            finished = handle.wait(CLINGO_TIMEOUT)
            if not finished:
                handle.cancel()
                handle.wait()
                logger.warning(
                    f"Clingo solve() timed out ({len(models)} models so far)"
                )

        models = [[str(atom) for atom in m] for m in models]
        logger.debug(f"Clingo: {len(models)} answer set(s)")
        return None, models
