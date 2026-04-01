---
name: Agentic Syntax-Fix Loop
description: Design, implementation decisions, and empirical results for the tool-using syntax-agent added to the pipeline.
---

# Agentic Syntax-Fix Loop

## Motivation

Early runs on puzzle `8d510a79` showed 6 Clingo parse errors in the initially generated program. The refinement loop was never told to fix syntax — it focused on logic — so syntax errors persisted across all 5 attempts, and the puzzle was never solved. The solution was to add a dedicated **syntax agent** that runs *before* the refinement loop, using tool calls to iteratively test and patch the program.

---

## New Files

| File | Purpose |
|---|---|
| `agent.py` | Core agentic multi-turn loop (`run_syntax_agent`) |
| `tools.py` | Tool implementations: `run_clingo`, `edit_code` |
| `prompts/syntax_agent.txt` | System prompt for the syntax agent |
| `docs/asp_syntax_guide.md` | Reference guide of 8 common Clingo syntax errors |
| `run_single.job` | SLURM script for single-puzzle runs on gpu_h100 |

---

## Modified Files

| File | Change |
|---|---|
| `config.py` | Added `MAX_SYNTAX_ATTEMPTS = 6`, `SYNTAX_GUIDE_PATH`, syntax_agent in `PROMPT_PATHS`, `ALLOWED_LOGGERS` |
| `pipeline.py` | `load_prompts()` strips YAML frontmatter from `asp_syntax_guide.md` and injects it into the syntax agent prompt via `<SYNTAX_GUIDE>` placeholder |
| `main.py` | Syntax-fix stage between initial verification and refinement loop; stores all agent steps in `records[i]["syntax_agent"]` |
| `prompts/5_reattempt.txt` | Updated to focus on logic/semantics; added fallback instruction for residual syntax errors |

---

## Key Design Decisions and Why

### 1. `edit_code` is in-memory only; `file_path` is ignored
**Why:** The pipeline holds the program as a string, not a file on disk. Accepting a `file_path` parameter was kept for API compatibility with the model's expectations but has no effect.

### 2. `content` (full rewrite) is disabled in `edit_code`
**Why:** Observed empirically. In run `21442106`, the model's first `edit_code` call with `content=...` shrank the program from 6481 → 3101 chars, discarding logic and introducing new errors. A second call went 3101 → 3005 chars. Each rewrite degraded the program. The targeted-patch path (`old_str`/`new_str`) preserved program size at 6467 chars while reducing parse errors from 6 to 2.

**Implementation:** `edit_code` returns an error string redirecting the model to use `old_str`/`new_str` if `content` is passed. The tool definition also omits `content` so the model is less likely to try it.

### 3. Prompt uses concrete format examples, not placeholders
**Why:** The model was emitting `<function=function run_clingo>` (prefixing the name with "function") because the original prompt used `function_name` as a literal placeholder, which the model interpreted as part of the format. Replaced with concrete examples showing the exact XML syntax expected.

The `parse_tool_call` regex also handles the malformed prefix:
```python
r"<function=(?:function\s+)?([\w]+)>"
```

### 4. Loop `continue`s when model gives no-tool-call response but errors remain
**Why:** Original code always `break`-ed when the model gave a "final answer" without a tool call. But the model sometimes extracts a code block directly as its answer. If that code still has errors, we need to feed it back and try again. The loop now only breaks if syntax is clean or max attempts is reached.

### 5. MAX_SYNTAX_ATTEMPTS = 6 (up from 4)
**Why:** In run `21441xxx`, rounds 3 and 4 of 4 both called `run_clingo` on the same program without making progress — the model was "re-confirming" errors rather than fixing them. Increasing to 6 gives more room for actual fix attempts. The prompt was also updated: "Use `run_clingo` to test a POTENTIAL fix, not to re-confirm errors you already know."

### 6. Syntax guide loaded at pipeline init; injected at prompt load time
**Why:** Keeps the guide in one authoritative location (`docs/asp_syntax_guide.md`) that can be updated without touching the prompt file. Injection via `<SYNTAX_GUIDE>` placeholder in `pipeline.py:load_prompts()`.

---

## Tool Implementations

### `run_clingo(code, num_models=1, extra_args="")`
Uses the Python Clingo API (not subprocess). Formats output as a human-readable string:
- `PARSE ERROR: ...` — syntax errors before grounding
- `GROUND ERROR: ...` — unsafe variables, undefined predicates caught at grounding
- `UNSATISFIABLE` — constraints are too strong
- `Answer set 1: pred(X,Y) ...` — satisfying answer sets

### `edit_code(current_program, file_path, content, old_str, new_str)`
In-memory string replacement.
- `content` → returns error message: "Use old_str + new_str for surgical patches."
- `old_str`/`new_str` → finds first occurrence, replaces, returns `(new_program, result)` where result includes the full updated program (so the model can see its changes).

---

## Nemotron Tool-Use Protocol

Nemotron-Cascade-2 requires tools defined as XML inside the system prompt. The model emits `<tool_call>` blocks; results are injected as user messages with `<tool_response>` tags. The `<think>` block is stripped before storing in multi-turn history (handled by `_split_thinking` in `nemotron_engine.py`).

Tool definition format (in system prompt):
```xml
<tools>
<tool_description>
<tool_name>run_clingo</tool_name>
<description>...</description>
<parameters>
  <parameter><name>code</name><type>string</type><required>true</required></parameter>
</parameters>
</tool_description>
</tools>
```

---

## Result Structure in JSON

The `syntax_agent` field in each run record is a dict:
```json
{
  "triggered": true,
  "initial_error": "<block>:35:17-18: error: ...",
  "syntax_fixed": false,
  "steps": [
    {
      "round": 1,
      "thinking": "...",
      "tool_call": {"name": "edit_code", "params": {...}},
      "tool_result": "...",
      "program_after": "...",
      "syntax_error_after": "<block>:67:..."
    }
  ]
}
```

---

## Empirical Results

### Run 21442106 — first successful run
- Syntax agent: 4 rounds (run_clingo, edit_code, run_clingo×2), errors reduced 6 → 3
- Refinement loop: solved on **attempt 3**
- Baseline without syntax agent: never solved in 5 attempts

### Run 21445746 (results/20260401_190103.json) — final implementation
- Initial program: 6481 chars, **6 parse errors**
- Syntax agent: 6 rounds, errors reduced to **2** (syntax_fixed=False), program 6467 chars (targeted patches preserved size)
- Refinement attempts 1–4: model rewrote program progressively (6467→3841→2626→3151→2743), each introducing different clingo errors (grounding errors, unsafe variables — not parse errors, which is progress)
- **Refinement attempt 5: SOLVED** — all training examples correct, program 2248 chars

### Interpretation
The syntax agent partially fixes the program (6 errors → 2), but `syntax_fixed=False`. The refinement loop then rewrites the program over multiple attempts, eventually arriving at a clean solution. The key improvement over the no-agent baseline: the refinement loop gets programs with *fewer* initial errors and *different* error types (logic/grounding errors instead of parse errors), which gives the model better feedback to converge.

---

## Known Limitations

1. **Partial fix only**: MAX_SYNTAX_ATTEMPTS=6 is often insufficient to clear all errors. The agent makes 1-2 errors per round but the original program may have 6+ distinct issues.
2. **Model sometimes re-tests known errors**: Despite prompt instructions, the model occasionally calls `run_clingo` on the unchanged program, wasting rounds.
3. **`old_str` mismatches**: If the model's quoted `old_str` doesn't exactly match the program text (whitespace differences, Unicode), the patch silently fails.
4. **Refinement still rewrites**: The refinement loop doesn't know about the syntax agent's partial fixes; it gets the post-agent program as its starting point, but may ignore it and rewrite from scratch.
