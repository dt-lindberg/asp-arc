"""Syntax-fix routines for ASP programs.

Three-stage approach:
1. quick_syntax_fix  — deterministic regex fixes (no LLM)
2. rewrite_syntax_fix — single-shot LLM rewrite (fast, direct)
3. run_syntax_agent   — multi-turn tool-call loop (fallback)

Tool calls in stage 3 are emitted as <tool_call> XML blocks.
Tool responses are injected back as user messages with <tool_response> tags,
following the Nemotron tool-use protocol (docs/nemotron_tool_usage.md).
"""

import os
import re

from config import SYNTAX_GUIDE_PATH
from eval import _check_syntax, _annotate_clingo_error
from logger import get_logger
from tools import edit_code as _edit_code
from tools import run_clingo as _run_clingo
from utils import extract_code_blocks


def _load_syntax_guide():
    """Load the ASP syntax guide, stripping YAML frontmatter."""
    if not os.path.isfile(SYNTAX_GUIDE_PATH):
        return ""
    with open(SYNTAX_GUIDE_PATH, encoding="utf-8") as f:
        raw = f.read()
    if raw.startswith("---"):
        end = raw.find("---", 3)
        return raw[end + 3 :].strip() if end != -1 else raw
    return raw.strip()


_SYNTAX_GUIDE = _load_syntax_guide()

logger = get_logger("agent")


def quick_syntax_fix(program):
    """Apply deterministic regex fixes for the most common LLM-generated syntax errors.

    These fixes are safe to apply without LLM reasoning:
    1. C #mod N  →  C \\ N    (wrong modulo operator)
    2. { ... } exactly N  →  N { ... } N  (wrong cardinality form)
    3. #aggr{...} = Var  →  Var = #aggr{...}  (inverted aggregate, only safe for simple cases)

    Returns:
        (fixed_program, n_fixes)  where n_fixes counts applied substitutions.
    """
    n_fixes = 0
    result = program

    # Fix 1: C #mod N → C \\ N  (e.g., "C #mod 2" → "C \\ 2")
    fixed, count = re.subn(r"(\b\w+)\s+#mod\s+(\w+)", r"\1 \\ \2", result)
    if count:
        result = fixed
        n_fixes += count
        logger.info(f"  [quick_fix] Fixed {count} '#mod' → '\\\\' replacement(s)")

    # Fix 2: { ... } exactly N :- → N { ... } N :-
    # Match: { ... } exactly <digit> :-
    fixed, count = re.subn(
        r"\{\s*(.*?)\s*\}\s+exactly\s+(\d+)\s*:-",
        lambda m: f"{m.group(2)} {{ {m.group(1)} }} {m.group(2)} :-",
        result,
        flags=re.DOTALL,
    )
    if count:
        result = fixed
        n_fixes += count
        logger.info(
            f"  [quick_fix] Fixed {count} 'exactly N' → 'N {{ }} N' replacement(s)"
        )

    # Fix 3: #const UPPERCASE = N → #const lowercase = N
    fixed, count = re.subn(
        r"#const\s+([A-Z][A-Z0-9_]*)\s*=",
        lambda m: f"#const {m.group(1).lower()} =",
        result,
    )
    if count:
        result = fixed
        n_fixes += count
        logger.info(f"  [quick_fix] Fixed {count} uppercase #const name(s)")

    # Fix 4: #aggr { ... } = Var → Var = #aggr { ... }  (inverted aggregate assignment)
    fixed, count = re.subn(
        r"(#(?:count|sum|min|max))\s*(\{[^}]+\})\s*=\s*([A-Z]\w*)",
        r"\3 = \1 \2",
        result,
    )
    if count:
        result = fixed
        n_fixes += count
        logger.info(
            f"  [quick_fix] Fixed {count} inverted aggregate(s) (#aggr{{}} = Var → Var = #aggr{{}})"
        )

    # Fix 5: #aggr(...) → #aggr{...}  (parentheses instead of curly braces in aggregates)
    # Handles one level of nested parentheses (e.g., #count(X : pred(Y)))
    fixed, count = re.subn(
        r"(#(?:count|sum|min|max))\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)",
        r"\1 { \2 }",
        result,
    )
    if count:
        result = fixed
        n_fixes += count
        logger.info(
            f"  [quick_fix] Fixed {count} aggregate(s) using () instead of {{}}"
        )

    # Fix 6: aggregate(Var = #aggr, Condition, Var) → Var = #aggr { _ : Condition }
    # SWI-Prolog aggregate/3 → Clingo aggregate syntax
    fixed, count = re.subn(
        r"aggregate\s*\(\s*([A-Z]\w*)\s*=\s*(#(?:count|sum|min|max))\s*,\s*(.+?)\s*,\s*\1\s*\)",
        r"\1 = \2 { _ : \3 }",
        result,
        flags=re.DOTALL,
    )
    if count:
        result = fixed
        n_fixes += count
        logger.info(
            f"  [quick_fix] Fixed {count} aggregate/3 call(s) (SWI-Prolog → Clingo)"
        )

    # Fix 7: aggregate_all(#aggr, Condition, Var) → Var = #aggr { _ : Condition }
    fixed, count = re.subn(
        r"aggregate_all\s*\(\s*(#(?:count|sum|min|max))\s*,\s*(.+?)\s*,\s*([A-Z]\w*)\s*\)",
        r"\3 = \1 { _ : \2 }",
        result,
        flags=re.DOTALL,
    )
    if count:
        result = fixed
        n_fixes += count
        logger.info(
            f"  [quick_fix] Fixed {count} aggregate_all/3 call(s) (SWI-Prolog → Clingo)"
        )

    return result, n_fixes


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: single-shot LLM rewrite
# ─────────────────────────────────────────────────────────────────────────────

_REWRITE_SYSTEM = (
    "You are a Clingo/ASP syntax expert. "
    "Fix ONLY syntax errors — never change program logic or semantics.\n\n"
    + _SYNTAX_GUIDE
)

_REWRITE_GUIDE = """\
Common Clingo syntax fixes (apply immediately):
  aggregate(N=#count,cond,N)    →  N = #count { _ : cond }
  aggregate_all(#count,cond,N)  →  N = #count { _ : cond }
  C #mod 2                      →  C \\ 2
  #min{...} = Var               →  Var = #min{...}
  { } exactly N :-              →  N { } N :-
  #count(...)                   →  #count{...}
  #sum(...)                     →  #sum{...}
  #const UPPER = N.             →  #const upper = N.
  X = #max{...}.  (standalone)  →  max_val(X) :- X = #max{...}.
  #count{A} = #count{B} (body)  →  N = #count{A}, N = #count{B}  (bind one side first)
"""


def rewrite_syntax_fix(program, syntax_error, engine, pipeline, max_rewrites=3):
    """Attempt to fix syntax errors via direct single-shot LLM rewrites.

    Much faster than the multi-turn tool loop and avoids the tool-calling
    failure modes (edit_code old_str mismatches, run_clingo over-use).

    Args:
        program:       Current ASP program string (may have syntax errors).
        syntax_error:  Clingo error string.
        engine:        NemotronEngine instance.
        pipeline:      Pipeline instance (for _check_syntax calls).
        max_rewrites:  Maximum rewrite attempts before giving up.

    Returns:
        (fixed_program, n_rounds_used, final_error)
        fixed_program: best program state at exit.
        n_rounds_used: how many LLM calls were made.
        final_error:   None if syntax clean, else the remaining error string.
    """
    current_program = program
    current_error = syntax_error

    for attempt in range(1, max_rewrites + 1):
        annotated = _annotate_clingo_error(current_error)
        logger.info(f"  [rewrite] attempt {attempt}/{max_rewrites}")

        user_content = (
            f"{_REWRITE_GUIDE}\n\n"
            f"Fix the Clingo syntax errors in this program. "
            f"Output the COMPLETE fixed program in ONE ```asp code block.\n\n"
            f"Program:\n```asp\n{current_program}\n```\n\n"
            f"Clingo errors:\n{annotated}"
        )
        messages = [
            {"role": "system", "content": _REWRITE_SYSTEM},
            {"role": "user", "content": user_content},
        ]

        thinking, response = engine.generate_batch([messages])[0]
        extracted = extract_code_blocks(response)

        if extracted:
            current_program = extracted
            logger.info(f"  [rewrite] extracted program ({len(current_program)} chars)")
        else:
            logger.info(
                "  [rewrite] no code block in response — keeping current program"
            )

        err = _check_syntax(current_program, pipeline)
        if err is None:
            logger.info(f"  [rewrite] syntax clean after {attempt} rewrite(s)")
            return current_program, attempt, None

        logger.info(f"  [rewrite] still has errors after attempt {attempt}")
        current_error = err

    return current_program, max_rewrites, current_error


def parse_tool_call(text):
    """Parse a <tool_call> XML block from model output.

    Returns:
        {"name": str, "params": dict}  if a tool call is found.
        None                            if no tool call block is present.
    """
    tc = re.search(r"<tool_call>(.*?)</tool_call>", text, re.DOTALL)
    if not tc:
        return None
    # Accept both "<function=run_clingo>" and "<function=function run_clingo>"
    # (some model outputs prefix the name with the word "function")
    fn = re.search(
        r"<function=(?:function\s+)?([\w]+)>(.*?)</function>", tc.group(1), re.DOTALL
    )
    if not fn:
        return None
    params = {
        m.group(1): m.group(2).strip()
        for m in re.finditer(
            r"<parameter=(\w+)>(.*?)</parameter>", fn.group(2), re.DOTALL
        )
    }
    if "num_models" in params:
        try:
            params["num_models"] = int(params["num_models"])
        except ValueError:
            pass
    return {"name": fn.group(1), "params": params}


def run_syntax_agent(
    program, syntax_error, system_prompt, engine, pipeline, max_attempts=4
):
    """Run the agentic syntax-fix loop for one ASP program.

    Each round:
      1. Call the LLM (single-item batch, full conversation history).
      2. Parse any tool call from the response.
      3. Execute the tool (run_clingo or edit_code) and update current_program.
      4. Inject the tool result back as a user message.
      5. Repeat until: no tool call (model done), syntax clean, or max_attempts reached.

    If the model emits no tool call, any ```asp block in the response is extracted
    as the new program.

    Per Nemotron multi-turn docs: the engine's generate_batch already strips
    <think>...</think> from responses, so history stays clean.

    Args:
        program:       ASP program string (contains syntax errors)
        syntax_error:  Clingo error string from initial _check_syntax call
        system_prompt: Full syntax-agent prompt (tool defs + syntax guide)
        engine:        NemotronEngine — called directly (no pipeline caching)
        pipeline:      Pipeline instance — used for intermediate _check_syntax calls
        max_attempts:  Max tool-call rounds before giving up

    Returns:
        (fixed_program, steps)
        fixed_program: best program state at exit (may still have errors if exhausted)
        steps:         list of per-round dicts for storage in results JSON
    """
    current_program = program

    annotated_error = _annotate_clingo_error(syntax_error)

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"Fix the Clingo syntax errors in this ASP program.\n\n"
                f"Program:\n```asp\n{current_program}\n```\n\n"
                f"Clingo errors:\n{annotated_error}"
            ),
        },
    ]

    steps = []
    prev_error_count = (
        syntax_error.count("\n") + 1
    )  # approximate error count from initial string
    stall_rounds = 0
    STALL_LIMIT = 4  # exit if no progress for this many consecutive rounds

    for attempt in range(1, max_attempts + 1):
        logger.info(f"  [syntax agent] round {attempt}/{max_attempts}")

        # Call the model (single-item batch preserves full conversation history)
        thinking, response = engine.generate_batch([messages])[0]
        messages.append({"role": "assistant", "content": response})

        tool_call = parse_tool_call(response)

        step = {
            "round": attempt,
            "thinking": thinking,
            "response": response,
            "tool_call": tool_call,
            "tool_result": None,
            "program_after": current_program,
            "syntax_error_after": None,
        }

        if tool_call is None:
            # Model gave a direct answer — extract any code block present
            logger.info("  [syntax agent] no tool call — extracting code block")
            extracted = extract_code_blocks(response)
            if extracted:
                current_program = extracted
                logger.info(
                    f"  [syntax agent] extracted program ({len(current_program)} chars)"
                )
            syntax_err_now = _check_syntax(current_program, pipeline)
            step["program_after"] = current_program
            step["syntax_error_after"] = syntax_err_now
            steps.append(step)

            if syntax_err_now is None:
                logger.info("  [syntax agent] syntax clean after direct fix — done")
                break

            if attempt >= max_attempts:
                logger.info(
                    "  [syntax agent] max attempts reached with remaining errors"
                )
                break

            # Still errors and rounds remain — ask the model to continue
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"The program still has syntax errors:\n{syntax_err_now}\n\n"
                        f"Current program:\n```asp\n{current_program}\n```\n\n"
                        f"Please fix the remaining errors. {max_attempts - attempt} round(s) remaining."
                    ),
                }
            )
            continue

        # Execute tool call
        name = tool_call["name"]
        params = tool_call["params"]
        logger.info(f"  [syntax agent] tool={name} params={list(params.keys())}")

        if name == "run_clingo":
            result_str = _run_clingo(
                code=params.get("code", ""),
                num_models=params.get("num_models", 1),
                extra_args=params.get("extra_args", ""),
            )
        elif name == "edit_code":
            current_program, result_str = _edit_code(
                current_program=current_program,
                file_path=params.get("file_path"),
                content=params.get("content"),
                old_str=params.get("old_str"),
                new_str=params.get("new_str", ""),
            )
        else:
            result_str = (
                f"Error: unknown tool '{name}'. Available tools: run_clingo, edit_code."
            )

        logger.info(f"  [syntax agent] result: {result_str[:150]}")

        # Check syntax of the (possibly updated) current program
        syntax_err_now = _check_syntax(current_program, pipeline)

        # Track progress: count approximate error lines to detect stalls
        if syntax_err_now is not None:
            curr_error_count = syntax_err_now.count("\n") + 1
            if curr_error_count >= prev_error_count:
                stall_rounds += 1
                logger.info(
                    f"  [syntax agent] no progress ({curr_error_count} error lines, "
                    f"stall {stall_rounds}/{STALL_LIMIT})"
                )
            else:
                stall_rounds = 0  # reset stall counter on any progress
                logger.info(
                    f"  [syntax agent] progress: {prev_error_count} → {curr_error_count} error lines"
                )
            prev_error_count = curr_error_count

        step["tool_result"] = result_str
        step["program_after"] = current_program
        step["syntax_error_after"] = syntax_err_now
        steps.append(step)

        if syntax_err_now is None:
            logger.info("  [syntax agent] syntax clean — exiting loop early")
            break

        if stall_rounds >= STALL_LIMIT:
            logger.info(
                f"  [syntax agent] stuck for {STALL_LIMIT} rounds — giving up early"
            )
            break

        # Build tool response; append a status note if errors remain and rounds are left
        tool_response_content = result_str
        if syntax_err_now is not None and attempt < max_attempts:
            tool_response_content += (
                f"\n\n---\nThe program still has syntax errors:\n{syntax_err_now}\n"
                f"Continue fixing. {max_attempts - attempt} round(s) remaining."
            )

        messages.append(
            {
                "role": "user",
                "content": f"<tool_response>\n{tool_response_content}\n</tool_response>",
            }
        )

    return current_program, steps
