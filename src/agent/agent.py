"""
Agent: the LLM + its harness.

* Owns the vLLM engine and the prompt templates.
* Formats puzzles into prompts, calls the engine in a batch, and returns the raw
  thinking, response, and extracted ASP program for each puzzle.
* Stateless across calls, per-puzzle history lives in utils.session.Session.
"""

from agent.utils import build_initial_user, build_reattempt_user, split_on_separator
from config.config_agent import PROMPT_PATHS
from utils.grids import extract_code_blocks
from utils.logger import get_logger

logger = get_logger(__name__)


class Agent:
    def __init__(self):
        """
        Load prompt templates eagerly; defer vLLM model load until first
        generation call so `python -c 'import agent.agent'` stays cheap.

        * The initial template is split into a static system part (context,
          rules, few-shot examples) and a user part (the puzzle + ask).
        * The reattempt template is a single user message (no system prompt);
          the history itself carries the conversational context.
        """
        self.prompts = {}
        for kind, path in PROMPT_PATHS.items():
            with open(path, encoding="utf-8") as f:
                self.prompts[kind] = f.read().strip()
        logger.debug(f"Agent loaded {len(self.prompts)} prompt templates")

        # Pre-split the initial template once so generate_initial is cheap.
        self._initial_system, self._initial_user = split_on_separator(
            self.prompts["initial"]
        )
        self._engine = None

    @property
    def engine(self):
        # Import lazily so the module graph stays loadable in environments without
        # vLLM / huggingface_hub (e.g. a local dev venv with no GPU).
        if self._engine is None:
            from agent.vllm_engine import VLLMEngine

            self._engine = VLLMEngine()
        return self._engine

    def generate_initial(self, puzzles):
        """
        Produce an initial ASP program for each puzzle.

        * System prompt is the static context + rules + few-shot examples.
        * User prompt is the puzzle's training examples + ask.
        * Returns a list of (prompt, thinking, response, program) tuples in
          puzzle-input order, where `prompt` is the full system+user text for
          logging.
        """
        user_prompts = [build_initial_user(self._initial_user, p) for p in puzzles]
        messages = [
            [
                {"role": "system", "content": self._initial_system},
                {"role": "user", "content": user},
            ]
            for user in user_prompts
        ]
        outputs = self.engine.generate_batch(messages)
        full_prompts = [self._initial_system + "\n\n" + user for user in user_prompts]
        return [
            (prompt, thinking, response, extract_code_blocks(response))
            for prompt, (thinking, response) in zip(full_prompts, outputs)
        ]

    def generate_reattempt(self, puzzles, histories):
        """
        Produce a fresh ASP program informed by each puzzle's failed-attempt
        history.

        * System prompt is reused from the initial template so the model keeps
          access to the context, rules, and few-shot examples across attempts.
        * `histories[i]` is a list of (program, feedback_str) tuples, oldest first.
        * Returns a list of (prompt, thinking, response, program) tuples aligned
          with `puzzles` / `histories`.
        """
        template = self.prompts["reattempt"]
        user_prompts = [
            build_reattempt_user(template, puzzle, history)
            for puzzle, history in zip(puzzles, histories)
        ]
        messages = [
            [
                {"role": "system", "content": self._initial_system},
                {"role": "user", "content": user},
            ]
            for user in user_prompts
        ]
        outputs = self.engine.generate_batch(messages)
        full_prompts = [self._initial_system + "\n\n" + user for user in user_prompts]
        return [
            (prompt, thinking, response, extract_code_blocks(response))
            for prompt, (thinking, response) in zip(full_prompts, outputs)
        ]
