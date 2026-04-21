"""
Agent: the LLM + its harness.

* Owns the vLLM engine and the prompt templates.
* Formats puzzles into prompts, calls the engine in a batch, and returns the raw
  thinking, response, and extracted ASP program for each puzzle.
* Stateless across calls, per-puzzle history lives in utils.session.Session.
"""

from config.config_agent import MAX_MODEL_LEN, MAX_TOKENS, PROMPT_PATHS
from utils.grids import extract_code_blocks, format_examples_for_prompt
from utils.logger import get_logger

logger = get_logger(__name__)


class Agent:
    def __init__(self):
        """
        Load the two prompt templates eagerly; defer vLLM model load until first
        generation call so `python -c 'import agent.agent'` stays cheap.
        """
        self.prompts = {}
        for kind, path in PROMPT_PATHS.items():
            with open(path, encoding="utf-8") as f:
                self.prompts[kind] = f.read().strip()
        logger.debug(f"Agent loaded {len(self.prompts)} prompt templates")
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

        * Returns a list of (prompt, thinking, response, program) tuples in
          puzzle-input order.
        """
        prompts = [self._build_initial_prompt(p) for p in puzzles]
        messages = [[{"role": "user", "content": p}] for p in prompts]
        outputs = self.engine.generate_batch(messages)
        return [
            (prompt, thinking, response, extract_code_blocks(response))
            for prompt, (thinking, response) in zip(prompts, outputs)
        ]

    def generate_reattempt(self, puzzles, histories):
        """
        Produce a fresh ASP program informed by each puzzle's failed-attempt
        history.

        * `histories[i]` is a list of (program, feedback_str) tuples, oldest first.
        * Returns a list of (prompt, thinking, response, program) tuples aligned
          with `puzzles` / `histories`.
        """
        system, instruction = self._split_reattempt_template()
        prompts = [
            self._build_reattempt_prompt(system, instruction, puzzle, history)
            for puzzle, history in zip(puzzles, histories)
        ]
        messages = [
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ]
            for prompt in prompts
        ]
        outputs = self.engine.generate_batch(messages)
        return [
            (prompt, thinking, response, extract_code_blocks(response))
            for prompt, (thinking, response) in zip(prompts, outputs)
        ]

    def _build_initial_prompt(self, puzzle):
        return self.prompts["initial"].replace(
            "==EXAMPLES==", format_examples_for_prompt(puzzle["train"])
        )

    def _split_reattempt_template(self):
        """Split the reattempt template into system and user parts on ===SEPARATOR===."""
        system, instruction = self.prompts["reattempt"].split("===SEPARATOR===")
        return system.strip(), instruction.strip()

    def _build_reattempt_prompt(self, system, instruction, puzzle, history):
        """
        Fill the user-part template with the puzzle's examples and the failed-attempt
        history.

        * Guards against context overflow by truncating the oldest program in the
          history to 500 chars when the assembled prompt exceeds the budget
          (MAX_MODEL_LEN - MAX_TOKENS). Feedback is kept intact.
        """
        examples = format_examples_for_prompt(puzzle["train"])
        history_parts = [
            f"<attempt_{idx}>\n<asp>\n{program}\n</asp>\n\n"
            f"<feedback>\n{feedback}\n</feedback>\n</attempt_{idx}>"
            for idx, (program, feedback) in enumerate(history, start=1)
        ]
        history_str = "\n\n".join(history_parts) if history_parts else "(none)"

        prompt = instruction.replace("==EXAMPLES==", examples).replace(
            "==HISTORY==", history_str
        )
        full = system + "\n\n" + prompt

        # Rough 1 token ≈ 4 chars heuristic; leave MAX_TOKENS budget for the response.
        char_budget = (MAX_MODEL_LEN - MAX_TOKENS) * 4
        if len(full) > char_budget and len(history) > 1:
            logger.warning(
                f"Reattempt prompt ({len(full)} chars) exceeds char budget "
                f"({char_budget}). Truncating oldest program in history."
            )
            old_prog, old_feedback = history[0]
            truncated = old_prog[:500] + "\n... [truncated for context budget]"
            history_parts[0] = (
                f"<attempt_1>\n<asp>\n{truncated}\n</asp>\n\n"
                f"<feedback>\n{old_feedback}\n</feedback>\n</attempt_1>"
            )
            history_str = "\n\n".join(history_parts)
            prompt = instruction.replace("==EXAMPLES==", examples).replace(
                "==HISTORY==", history_str
            )
            full = system + "\n\n" + prompt

        return prompt
