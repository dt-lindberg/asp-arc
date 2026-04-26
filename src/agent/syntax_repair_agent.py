"""Batched per-round syntax-repair agent loop.

For each active puzzle the agent:
  1. Renders the conversation-so-far into a prompt.
  2. Sends the batch of prompts to vLLM in one call.
  3. Parses <tool_call> blocks from each response.
  4. Dispatches them sequentially against that puzzle's program state.
  5. Runs Clingo, checks halt conditions.
  6. Repeats until all puzzles halt or the round cap fires.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from agent.syntax_repair_prompt import SYSTEM_PROMPT
from agent.syntax_repair_tools import (
    ToolResult,
    dispatch_batch,
    extract_error_lines,
)
from agent.syntax_repair_transcript import Round, ToolCall, Transcript
from config.config_agent import MAX_TOKENS, MAX_MODEL_LEN
from utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tool-call parsing
# ---------------------------------------------------------------------------

_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*\n?(.*?)\n?\s*</tool_call>", re.DOTALL
)

# Fallback: model sometimes emits bare tool names wrapped in angle brackets
# like <edit_lines>args</edit_lines> instead of wrapping in <tool_call>
_BARE_TOOL_RE = re.compile(
    r"<(edit_lines|read_program|clingo_check|give_up)>\s*\n?(.*?)\n?\s*</\1>", re.DOTALL
)

# Some models double-prefix: <function=function tool_name>
_FUNCTION_TOOL_RE = re.compile(
    r"<function=function\s+(\w+)>\s*\n?(.*?)\n?\s*</function=function\s+\1>", re.DOTALL
)


def _try_parse_json(text: str) -> dict | None:
    import json as _json
    text = text.strip()
    # Strip trailing content after the first complete JSON object
    # (model sometimes adds commentary after the closing brace)
    if text.startswith("{"):
        depth = 0
        end = -1
        for i, ch in enumerate(text):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > 0:
            text = text[:end]
    try:
        return _json.loads(text)
    except _json.JSONDecodeError:
        return None


def parse_tool_calls(response: str) -> tuple[list[tuple[str, dict]], list[str]]:
    """Extract all <tool_call> blocks from a response string.

    Returns (calls, failures) where calls is [(name, args_dict), ...] and
    failures is a list of strings describing blocks that looked like tool
    calls but didn't parse successfully.
    """
    calls: list[tuple[str, dict]] = []
    failures: list[str] = []

    # 1. Try standard <tool_call> blocks
    blocks = _TOOL_CALL_RE.findall(response)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n", 1)
        name = lines[0].strip()
        args_str = lines[1].strip() if len(lines) > 1 else "{}"
        args = _try_parse_json(args_str)
        if args is not None and isinstance(args, dict):
            calls.append((name, args))
        else:
            failures.append(
                f"Tool call '{name}' had invalid args: {args_str[:100]}"
            )

    # 2. Fallback: bare tool tags like <edit_lines>...</edit_lines>
    if not calls:
        bare_blocks = _BARE_TOOL_RE.findall(response)
        for name, args_str in bare_blocks:
            name = name.strip()
            args_str = args_str.strip() if args_str else "{}"
            args = _try_parse_json(args_str)
            if args is not None and isinstance(args, dict):
                calls.append((name, args))
            else:
                failures.append(
                    f"Bare tool tag '<{name}>' had invalid args: {args_str[:100]}"
                )

    # 3. Fallback: function=function prefix
    if not calls:
        func_blocks = _FUNCTION_TOOL_RE.findall(response)
        for name, args_str in func_blocks:
            name = name.strip()
            args_str = args_str.strip() if args_str else "{}"
            args = _try_parse_json(args_str)
            if args is not None and isinstance(args, dict):
                calls.append((name, args))
            else:
                failures.append(
                    f"Function tool '{name}' had invalid args: {args_str[:100]}"
                )

    return calls, failures


# ---------------------------------------------------------------------------
# Per-puzzle state
# ---------------------------------------------------------------------------

@dataclass
class PuzzleState:
    puzzle_id: str
    source_audit: str
    initial_program: str  # frozen copy for transcript
    program: str          # mutable, edited by tools
    initial_errors: list[str]
    conversation: list[dict] = field(default_factory=list)  # role/content messages
    rounds: list[Round] = field(default_factory=list)
    halt_reason: str = ""
    prev_error_set: frozenset[str] | None = None
    rounds_since_progress: int = 0


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class SyntaxRepairAgent:
    MAX_ROUNDS = 6
    MAX_CONTEXT_TOKENS = MAX_MODEL_LEN - MAX_TOKENS

    def __init__(self, seed=None) -> None:
        self._engine = None
        self.seed = seed

    @property
    def engine(self):
        if self._engine is None:
            from agent.vllm_engine import VLLMEngine

            self._engine = VLLMEngine(seed=self.seed)
        return self._engine

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, puzzles: list[dict]) -> list[Transcript]:
        """Run the syntax-repair agent on a batch of puzzles.

        Each puzzle dict must have:
            puzzle_id: str
            source_audit: str (path to source JSON)
            program: str
            errors: list[str] (formatted Clingo error strings)

        Returns one Transcript per puzzle.
        """
        # Build states with unique keys in case multiple programs share a puzzle_id
        states: dict[str, PuzzleState] = {}
        seen_ids: dict[str, int] = {}
        for p in puzzles:
            pid = p["puzzle_id"]
            if pid in seen_ids:
                seen_ids[pid] += 1
                key = f"{pid}_{seen_ids[pid]}"
            else:
                seen_ids[pid] = 0
                key = pid
            states[key] = PuzzleState(
                puzzle_id=key,
                source_audit=p["source_audit"],
                initial_program=p["program"],
                program=p["program"],
                initial_errors=p["errors"],
                conversation=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _make_initial_user_message(p["program"], p["errors"])},
                ],
            )

        active_ids = list(states.keys())

        for round_idx in range(self.MAX_ROUNDS):
            if not active_ids:
                break

            logger.info(
                f"Round {round_idx}: {len(active_ids)} active puzzles"
            )

            active_states = [states[pid] for pid in active_ids]

            # 1. Build prompts
            prompts = [_build_prompt(s.conversation) for s in active_states]

            # 2. vLLM batch calls (sub-batch to avoid timeouts on large batches)
            SUB_BATCH_SIZE = 10
            all_outputs = []
            for sub_start in range(0, len(prompts), SUB_BATCH_SIZE):
                sub_prompts = prompts[sub_start : sub_start + SUB_BATCH_SIZE]
                sub_msgs = sub_prompts  # already in messages format
                logger.debug(
                    f"  Sub-batch {sub_start // SUB_BATCH_SIZE + 1}: "
                    f"{len(sub_msgs)} prompts"
                )
                t0 = time.perf_counter()
                sub_outputs = self.engine.generate_batch_with_tokens(
                    sub_msgs, max_tokens_override=16384
                )
                t_gen = time.perf_counter() - t0
                logger.debug(f"  Sub-batch took {t_gen:.1f}s")
                all_outputs.extend(sub_outputs)

            new_active: list[str] = []

            for state, (thinking, response, prompt_tokens, completion_tokens) in zip(active_states, all_outputs):
                if not response.strip():
                    # Empty response
                    state.halt_reason = "no_tool_calls"
                    _record_round(
                        state, round_idx, thinking, response,
                        [], [], [],
                        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                    )
                    logger.info(f"Puzzle {state.puzzle_id}: no_tool_calls (empty response)")
                    continue

                # 3. Parse tool calls
                calls_list, parse_failures = parse_tool_calls(response)

                # 4. Dispatch tools
                final_prog, results = dispatch_batch(calls_list, state.program)
                state.program = final_prog

                # 5. After dispatching all calls, check if give_up was signaled
                gave_up = any(r.halt for r in results)
                if gave_up:
                    state.halt_reason = "gave_up"
                    _record_round(
                        state, round_idx, thinking, response,
                        calls_list, results, parse_failures,
                        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                    )
                    logger.info(f"Puzzle {state.puzzle_id}: gave_up")
                    continue

                if not calls_list:
                    state.halt_reason = "no_tool_calls"
                    _record_round(
                        state, round_idx, thinking, response,
                        [], [], parse_failures,
                        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                    )
                    logger.info(f"Puzzle {state.puzzle_id}: no_tool_calls (no blocks found)")
                    continue

                # 6. Run Clingo on resulting program
                from agent.syntax_repair_tools import _clingo_check

                clingo_result = _clingo_check({}, state.program)
                errors_after = clingo_result.message.split("\n")
                if errors_after and errors_after[0].startswith("clingo_check errors:"):
                    errors_after = errors_after[1:]  # strip header
                error_set = extract_error_lines("\n".join(errors_after))

                logger.info(
                    f"Puzzle {state.puzzle_id}: round {round_idx}, "
                    f"{len(calls_list)} tool calls, {len(parse_failures)} parse failures, "
                    f"{len(error_set)} unique errors"
                )

                # Record round
                _record_round(
                    state, round_idx, thinking, response,
                    calls_list, results, parse_failures,
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                    errors_after=errors_after,
                )

                # 7. Check halt conditions
                if clingo_result.message.startswith("clingo_check: No errors"):
                    state.halt_reason = "fixed"
                    logger.info(f"Puzzle {state.puzzle_id}: FIXED!")
                    continue

                if error_set == state.prev_error_set:
                    state.halt_reason = "no_progress"
                    logger.info(f"Puzzle {state.puzzle_id}: no_progress")
                    continue

                state.prev_error_set = error_set

                # 8. Append assistant + tool_response messages to conversation
                state.conversation.append(
                    {"role": "assistant", "content": response}
                )
                tool_response_text = _build_tool_responses(results)
                state.conversation.append(
                    {"role": "user", "content": tool_response_text}
                )

                new_active.append(state.puzzle_id)

            active_ids = new_active

        # Finalize: any remaining active puzzles hit the round cap
        for pid in active_ids:
            states[pid].halt_reason = "round_cap"
            logger.info(f"Puzzle {pid}: round_cap")

        # Build transcripts
        transcripts = []
        for state in states.values():
            t = Transcript(
                puzzle_id=state.puzzle_id,
                source_audit=state.source_audit,
                initial_program=state.initial_program,
                initial_errors=state.initial_errors,
                halt_reason=state.halt_reason,
                rounds=state.rounds,
                final_program=state.program,
            )
            transcripts.append(t)

        return transcripts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_initial_user_message(program: str, errors: list[str]) -> str:
    lines = program.split("\n")
    numbered = "\n".join(f"{i + 1}: {line}" for i, line in enumerate(lines))
    err_text = "\n".join(errors)
    return (
        "<program>\n"
        f"{numbered}\n"
        "</program>\n\n"
        "<clingo_errors>\n"
        f"{err_text}\n"
        "</clingo_errors>"
    )


def _build_prompt(conversation: list[dict]) -> list[dict]:
    return conversation  # already in role/content format


def _build_tool_responses(results: list[ToolResult]) -> str:
    parts = []
    for r in results:
        parts.append(
            "<tool_response>\n"
            f"{r.message}\n"
            "</tool_response>"
        )
    return "\n\n".join(parts)


def _record_round(
    state: PuzzleState,
    round_idx: int,
    thinking: str,
    raw_response: str,
    calls_list: list[tuple[str, dict]],
    results: list[ToolResult],
    parse_failures: list[str],
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    errors_after: list[str] | None = None,
) -> None:
    tool_calls = []
    for (name, args), result in zip(calls_list, results):
        tool_calls.append(
            ToolCall(name=name, args=args, result=result.message)
        )

    round_data = Round(
        round_idx=round_idx,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        thinking=thinking,
        raw_response=raw_response,
        tool_calls=tool_calls,
        parse_failures=parse_failures,
        program_after=state.program,
        errors_after=errors_after or [],
    )
    state.rounds.append(round_data)
