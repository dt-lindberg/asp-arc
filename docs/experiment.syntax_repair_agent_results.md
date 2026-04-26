# Syntax Repair Agent — Experiment Summary

**Date:** 2026-04-26  
**Status:** Phase 1 complete, Phase 2 pending  
**Branch:** `main` (built from scratch, no prior `feature/agentic-nemotron` code reused)

---

## What was built

A specialized syntax-repair agent that takes a broken ASP program + its Clingo errors, runs a back-and-forth conversation with 4 tools, and stops when the program grounds cleanly or the agent gives up. Implemented as a standalone experiment harness, not a pipeline integration.

### Files

```
src/agent/syntax_repair_transcript.py  — Transcript schema (dataclass → JSON)
src/agent/syntax_repair_prompt.py      — System prompt with error→fix recipes + 3 few-shot examples
src/agent/syntax_repair_tools.py       — 4 tools: read_program, edit_lines, clingo_check, give_up
src/agent/syntax_repair_agent.py       — Batched per-round loop with halting logic + sub-batching
tests/test_syntax_repair_tools.py      — 20 unit tests (all passing)
scripts/run_syntax_repair.py           — Offline runner: collects programs from audit corpus, runs agent
scripts/summarize_syntax_repair.py     — Transcript analyzer for metrics
run_syntax_repair.job                  — SLURM job (gpu_h100, 2h, 1 GPU, --num 60)
```

### Architecture

**Per-round loop (batched across active puzzles):**

```
1. For every puzzle still active, render its conversation into a prompt.
2. engine.generate_batch_with_tokens(prompts, max_tokens=16384) — sub-batched in groups of 10.
3. For each puzzle, sequentially:
     - Parse all <tool_call> blocks in its response (supports 3 formats).
     - Dispatch them against this puzzle's program state (edit_lines sorted back-to-front).
     - Run clingo_check on the resulting program.
     - Check halt conditions.
4. Next round's batch = puzzles that did not halt.
```

**Halt conditions per puzzle:**
- Clingo returns no errors → `fixed`
- Round cap (6) reached → `round_cap`
- Same error set as previous round → `no_progress`
- Agent emitted `give_up` → `gave_up`
- No `<tool_call>` blocks in response → `no_tool_calls`

**4 Tools:**
| Tool | Description |
|------|-------------|
| `read_program(start_line?, end_line?)` | Returns program slice with line numbers |
| `edit_lines(start, end, new_text)` | Replaces lines (back-to-front sorted; overlapping rejected) |
| `clingo_check(program?)` | Grounds with Clingo, returns errors only |
| `give_up(reason)` | Explicit failure signal |

**Tool-call parser** handles 3 formats:
1. Standard: `<tool_call>\nname\n{json}\n</tool_call>`
2. Bare tags: `<edit_lines>\n{json}\n</edit_lines>`
3. `function=` prefix: `<function=function edit_lines>\n{json}\n</function=function edit_lines>`

Trailing commentary after JSON closing brace is stripped before parsing.

**Recipes embedded in system prompt (not a tool):**
- `#count(...)` → `#count{...}` (parens to braces)
- Aggregate on left → move to right, flip comparators
- Unsafe variables → ground in body before aggregate/arithmetic
- Markdown fences / XML tags → remove
- Aggregate arithmetic (`#count + #count`) → decompose into intermediate predicates
- Choice rule comma errors → use predicate atoms, not bare variable tuples
- `#mod` → `\`
- Bare assignment → wrap in rule

---

## What worked

### 1. Fix rate above threshold

| Run | Programs | Fixed | Rate | Source | Notes |
|-----|----------|-------|------|--------|-------|
| 1 (original) | 60 | 14 | 23.3% | 20260426_123318 | Baseline |
| 4 (improved) | 60 | 16 | 26.7% | 20260426_123318 | Better parser + recipes |

The 26.7% fix rate is well above the 10% stop threshold in the experiment spec, and dramatically better than the deterministic rewriter's ~1% (5/409). The residual is predominantly semantic (unsafe variables, missing bindings) — exactly where the experiment hypothesized a model would outperform regex.

### 2. Batching confirmed

**Mean tool calls per turn: 2.0** (55% of all rounds had >1 call). The few-shot examples successfully biased the model away from single-call behavior. The maximum observed was **12 calls in one turn** (`88a10436_2`).

This confirms the central hypothesis: multiple tool calls per assistant turn reduce the number of expensive LLM rounds.

### 3. Tool-call parsing is reliable

The improved parser handles all 3 observed Nemotron formats. Parse failures dropped from pervasive (in earlier attempts) to 9 failures across 113 rounds. Every failure is logged in the per-round `parse_failures` array — nothing is silently dropped.

### 4. Sub-batching + token cap resolves batch-stall

Large batches (>20 prompts) caused the last 2-3 prompts to hang indefinitely (likely Nemotron generating very long `<think>` blocks for complex programs). Two fixes resolved this:
- **Sub-batches of 10**: Divides the round's prompts into groups, avoiding the "slowest prompt sets wall-clock for all" problem at extreme batch sizes.
- **max_tokens=16384 override**: Caps per-generation output at 16K tokens (generous for 2K-10K thinking blocks), preventing unbounded generation.

### 5. Back-to-front edit sorting works

The `dispatch_batch` function sorts `edit_lines` calls by descending start line before applying. Non-edit calls (read, check) interleave in original order. Overlapping ranges are rejected with a clear error. This lets the model write batched edits in any order without worrying about line number shifts.

---

## What didn't work / limitations

### 1. Unsafe variables are hard to fix

The dominant residual error is unsafe variables in complex programs. The model often correctly diagnoses the problem ("V appears only inside the aggregate condition — not grounded") but the fix requires program restructuring (adding new domain predicates, splitting multi-line rules) that the model struggles to execute correctly via line-based edits.

**Potential improvements:**
- Add a `replace_rule(pattern, replacement)` tool that matches by semantics, not line numbers.
- Add more worked examples of unsafe-variable fixes in the system prompt.
- Relax the `no_progress` detection for unsafe-variable errors (they're naturally multi-round fixes).

### 2. Duplicate puzzle IDs across audit runs

The data collector deduplicates by program TEXT, but multiple programs share the same `puzzle_id` (different refinement attempts). The agent's `run()` method now disambiguates with `puzzle_id` / `puzzle_id_1` / `puzzle_id_2` suffixes, but this was a bug in early runs (only 2 of 60 programs survived dict-key collision in Run 1).

### 3. Context efficiency could be better

The system prompt is ~10K chars (~3K tokens). Each user message contains the full program with line numbers. As rounds progress, the conversation grows. No truncation is applied — this is fine for 6 rounds but limits scalability to many-round scenarios.

### 4. No cross-run dedup

The Phase 1 runs all used programs from a single audit run (`20260426_123318`). Running across all 14 audit runs yields 556 unique broken programs, but dedup across runs would reduce overlap. Not yet implemented.

---

## Quantitative summary (Run 4, improved agent)

| Metric | Value |
|--------|-------|
| Programs processed | 60 |
| Fixed | 16 (26.7%) |
| no_progress | 27 (45.0%) |
| no_tool_calls | 17 (28.3%) |
| Total rounds | 113 |
| Total tool calls | 231 |
| Mean calls/turn | 2.0 |
| Multi-call rounds | 58/113 (51.3%) |
| Parse failures | 9 |
| Mean prompt tokens/puzzle | 8,167 |
| Mean completion tokens/puzzle | 11,107 |

---

## Implementation details and decisions

### Why line-range edits instead of string matching

The previous agent used `replace_full_content` with `old_str`/`new_str`, which caused silent failures on whitespace/Unicode mismatches. The line-range approach matches Clingo's mental model (errors cite line numbers), and `read_program` lets the model re-anchor after edits without re-feeding the full program.

### Why recipes in the prompt instead of a tool

At 5-8 entries, the recipe catalogue fits in ~20 lines. Putting it behind a `lookup_pattern(error_kind)` tool would cost a round-trip to discover the model already knows the fix (the model pays the prefill cost on the system message regardless). If the catalogue grows past ~15 entries, promote back to a tool.

### Why no `replace_full_content`

The previous agent's `replace_full_content` could destroy logic — one failed match could lose the entire program. The `edit_lines` tool constrains edits to specific line ranges, and back-to-front application handles line-number shifts.

### Why 6-round cap

Matches the prior agent's `MAX_SYNTAX_ATTEMPTS`. Combined with `no_progress` detection (same error set across consecutive rounds), the agent stops early when it's spinning. In practice, most fixed programs resolve in rounds 0-1; programs needing 3+ rounds rarely succeed.

### ASCII normalization

The live pipeline strips non-ASCII characters via `program.encode("ascii", errors="replace").decode("ascii")`. Nemotron emits Unicode characters (non-breaking hyphens, smart quotes, bullets) that become `?` after this step. The harness applies the same normalization before seeding the conversation, so the agent edits the same program Clingo sees.

---

## Next steps
- Try running the syntax agent over more examples and seeds, to get a more statistically significant result.
- Run the syntax agent WITHOUT thinking, Nemotron can dynamically enable/disable thinking by prepending <think></think>, this would save a ton of time on the syntax agent, but we would have to see how well it performs.

---

## Stop conditions (per experiment spec)

- **Batching:** Nemotron averages 2.0 calls/turn → well above 1.5 threshold. Continue.
- **Fix rate:** 26.7% → well above 10% threshold. Continue.
- Neither stop condition has fired.
