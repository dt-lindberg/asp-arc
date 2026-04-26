# Experiment: specialized syntax-repair agent

## Context

The asp-arc pipeline produces ASP programs that fail Clingo verification.
The deterministic rewriter (`docs/experiment.deterministic_rewriter.md`,
implemented in `src/utils/rewriter.py`) only fixes ~1% of broken programs
end-to-end on the current pipeline; the dominant residual is **semantic**
(unsafe variables, missing bindings) — exactly where a model with
language understanding should outperform regex.

A previous attempt lived on `feature/agentic-nemotron`. Its main
problems:
- one tool call per assistant turn → many slow rounds
- `replace_full_content` could destroy logic (disabled there for that
  reason)
- silent `old_str` mismatches on whitespace / Unicode

The deterministic rewriter is **out of scope here** — we know where to
find it if we want to slot it back in as a preprocessing step later.

## Goal

A specialized syntax-repair agent that, given a broken program and its
Clingo errors, runs a back-and-forth conversation calling tools until
the program either grounds cleanly or the agent gives up.

## Scope: standalone experiment

This is **not a pipeline integration**. Same shape as the deterministic
rewriter experiment: a small isolated harness next to the existing
pipeline, run on stored example programs from the audit corpus, with
qualitative inspection before any empirical claims.

- **Inputs come from disk.** `(program, clingo_errors)` pairs replayed
  from `~/Projects/asp-arc/src/audit/*/*.json`, exactly as the
  deterministic rewriter does.
- **Transcripts dumped per puzzle** to
  `results/syntax_repair_runs/<puzzle_id>.json` — one file per puzzle
  containing the full conversation, every tool call, and the Clingo
  output per round. Read by hand first; aggregate metrics second.

## Per-puzzle state and conversation setup

For each puzzle in the batch the harness does this exactly once at
conversation start:

1. Load the program string from the audit JSON.
2. ASCII-normalize it (see below). This is the *initial* program; the
   raw audit string is discarded.
3. Run Clingo against the normalized program to get a fresh error list
   — we do **not** reuse the audit's recorded `clingo_errors` field.
   The audit string may be from a different Clingo version and its
   line/column numbers may not match the program text the agent will
   edit.
4. Seed the conversation: system prompt, then a user message containing
   the normalized program (with line numbers) and the fresh error
   output.

From then on the program lives **in memory** on the puzzle's state
object and is mutated in place by tool calls. There is no re-read from
disk during the loop.

### ASCII normalization

The live pipeline strips non-ASCII characters before grounding
(`src/utils/clingo.py:44`):

```python
program_clean = program.encode("ascii", errors="replace").decode("ascii")
```

Nemotron sprinkles non-breaking hyphens (`‑`), em-dashes (`—`), smart
quotes (`'`/`"`/`'`/`"`), bullets (`•`), and the occasional Greek letter
into comments and identifiers. `errors="replace"` turns each of those
into `?`, and Clingo's error line/column numbers refer to that
`?`-laced version — not the original LLM output. We apply the same
`encode/decode` once during step 2 above so the agent edits the same
program Clingo sees.

## Pipeline shape: batched LLM, serialized everything else

The LLM call is the expensive thing. Nemotron's `<think>` blocks are
2k–10k tokens, so paying that prefill+decode cost once for a batch of
puzzles is much better than once per puzzle. Everything else
(tool dispatch, edit application, Clingo) is local and cheap, and
runs serially per puzzle.

Per round:

```
1. For every puzzle still active, render its conversation into a prompt.
2. engine.gen_response_batch(prompts)  — one vLLM call, N puzzles.
3. For each puzzle, in any order:
     - parse all <tool_call> blocks in its response (re.findall)
     - dispatch them serially against this puzzle's program state
     - run clingo_check on the resulting program
     - decide whether the puzzle halts or continues to the next round
4. The next round's batch is the puzzles that did not halt.
```

The batch shrinks as puzzles finish. Round 1 might submit 5 prompts;
round 5 might submit 1. vLLM handles variable batch sizes, and the
existing engine already does this for candidate generation.

Each puzzle owns its own `conversation`, `program`, `last_errors`, and
round counter. There is no shared state across puzzles in the batch —
a tool call from puzzle 3 lands in puzzle 3's program and nowhere else.
The dispatcher just routes by puzzle index.

Per puzzle, halt when:
- Clingo returns no errors → `fixed`
- Round cap reached → `round_cap`
- Same error set as previous round → `no_progress`
- Agent emitted `give_up` → `gave_up`
- No `<tool_call>` blocks in the response → `no_tool_calls`

### Things to expect (and accept)

- **The slowest decoder in the batch sets the wall-clock for that
  round.** If puzzle 2 thinks for 8k tokens and the others for 2k, all
  four wait. We accept this — at batch size 5 the variance evens out
  and the alternative (per-request token caps) trades fix-rate for
  speed.
- **A chatty puzzle drags the batch.** One puzzle that refuses to call
  `give_up` and keeps making no-progress edits holds the batch open
  until the round cap fires. The round cap (start at 6) is the
  backstop; the no-progress halt catches the common case. We do not
  add cross-puzzle round budgets — that's a complexity trap.
- **One log per puzzle.** Five conversations interleaved in one log is
  unreadable. Each puzzle's transcript lands in its own JSON file
  under `results/syntax_repair_runs/`.

## Multiple tool calls per assistant message

The biggest lever vs. the previous agent. Each turn pays the full
thinking cost, so the way to reduce wall-clock per puzzle is fewer
turns, not faster tools.

Mechanics:
- `parse_tool_calls` switches from `re.search` to `re.findall`.
- All N calls are dispatched **sequentially**, in the order the model
  wrote them, against the same puzzle's program state. Later calls
  see the effects of earlier ones — including a `clingo_check` placed
  between two `edit_lines` calls, which sees the half-edited program.
- All N results come back as one user message containing N tagged
  `<tool_response>` blocks.
- The system prompt tells the model:
    - "you may emit several `<tool_call>` blocks in one turn"
    - "they will be executed in the order you wrote them; later edits
       and checks see the effects of earlier ones"
    - "results are returned together in one message"
    - "prefer batching independent edits"
- 2–3 few-shot turns in the system prompt show the assistant emitting
  4 tool calls at once, to bias the model away from its single-call
  habit.

Tool-call parsing is a known fragile spot — the prior agent had a
quirk where Nemotron emitted `<function=function run_clingo>`
double-prefix. Before trusting the new schema in the loop, run the
parser against a handful of real Nemotron responses and confirm every
intended call lands. Parse failures (a `<tool_call>`-looking block
that didn't yield a callable) are recorded per round in the
transcript, not silently dropped — otherwise we'll spend rounds
debugging "why isn't the model doing anything" when the answer is
"the parser ate it."

Open question: whether Nemotron actually batches when prompted. We
don't know yet — it's the central thing this experiment tests. If the
model averages near 1 tool call per turn even after prompt
engineering, that's a real ceiling and we'd revisit.

## Tools

Four tools — small enough to keep the model's choice space manageable,
large enough to cover the work. Common Clingo error recipes live in
the system prompt itself, not behind a tool (see "Recipes" below).

### `read_program(start_line?, end_line?)`
Returns the current program (or a slice) with line numbers prefixed.
Lets the model re-anchor after edits without us re-feeding the full
program every turn (saves tokens; sidesteps the "old_str whitespace
mismatch" silent failure).

### `edit_lines(start, end, new_text)`
Replaces lines `start..end` (1-indexed, inclusive) with `new_text`.
Line-range based because Clingo errors already cite line numbers
(`<block>:27:9-15`), so this matches the model's mental model.

> **\*Caveat — line numbers and multi-edit batches.** When the model
> emits several `edit_lines` calls in one turn, every edit after the
> first acts on a program whose line numbers may have shifted (insert
> or delete changes downstream line numbers). Mitigations:
>
> 1. **Apply edits back-to-front by `start` line.** The runtime sorts
>    the batch in descending order before applying so earlier edits
>    don't shift later ones. The model is told it can write batched
>    edits in any order. Start here.
> 2. **Reject overlapping ranges in a batch** with a clear error so
>    the model retries with a smaller batch.
>
> Even with both, the model can write a batch where edit B *depends
> on* edit A's effect (B references content A just inserted). We
> accept that risk; if it bites often, fall back to a `patches` tool
> that takes a list of edits and applies them as a single atomic
> transaction with the runtime computing line shifts.

### `clingo_check(program?)`
Runs grounding on the current program (or an ad-hoc snippet) and
returns errors only — never answer sets. Cheap.

### `give_up(reason)`
Explicit failure signal. Better than relying on the round cap because
the model owns the decision and we can mine the reasons later.

We deliberately **omit** `replace_full_content` (it destroyed logic in
the previous agent), and resist adding `add_constraint` / `delete_rule`
/ similar — every extra tool widens the choice space and discourages
batching.

### Recipes (in the system prompt, not a tool)

A short catalogue of known Clingo error → fix recipes drawn from
`docs/docs.clingo_syntax_guide.md`, embedded directly in the system
prompt. Initial entries:

- `unexpected_eq_after_aggregate` → put the aggregate on the right
- `unexpected_count_in_head` → bind the aggregate to a variable
- `unsafe_variable` → ground the variable in the body
- `lexer_backtick` → drop the markdown fence
- `mod_not_supported` → use `\` instead of `#mod`

A 5-entry catalogue isn't worth a round-trip: the entire thing fits
in the prompt at maybe 20 lines, the model already pays the prefill
cost on the system message, and it removes one source of confusion
("did the model know to call lookup?"). If the catalogue grows past
~15 entries — or starts pulling in long worked examples — we promote
it back to a `lookup_pattern(error_kind)` tool so the prompt doesn't
bloat across all puzzles.

## Halting and budgets

- **Round cap**: start at 6 (matches the prior agent's
  `MAX_SYNTAX_ATTEMPTS`); revisit once we have data.
- **No-progress halt**: if the set of clingo error lines returned by
  `clingo_check` is identical for two consecutive rounds, stop.
- **`give_up`**: agent-initiated; reason logged.
- **`no_tool_calls`**: the model responded without any `<tool_call>`
  block; treat as halted (it has nothing left to do).

## Files (proposed; nothing built yet)

```
src/agent/syntax_repair_agent.py     # batched per-round loop, halting
src/agent/syntax_repair_tools.py     # the 4 tools, per-puzzle dispatch
src/agent/syntax_repair_prompt.py    # system prompt + recipes + few-shot
src/agent/syntax_repair_transcript.py # JSON dump shape; built first
tests/test_syntax_repair_tools.py    # unit tests for each tool
scripts/run_syntax_repair.py         # offline runner over a corpus slice
results/syntax_repair_runs/          # per-puzzle transcript JSONs
results/syntax_repair_summary.csv    # one row per puzzle (phase 2)
```

### Transcript shape

Phase 1 lives or dies on how readable the per-puzzle JSON dumps are.
Define and look at the shape on a hand-faked transcript before wiring
the LLM call. One file per puzzle, schema sketch:

```
{
  "puzzle_id": "...",
  "source_audit": "<path>",
  "initial_program": "...",
  "initial_errors": [...],
  "halt_reason": "fixed|round_cap|no_progress|gave_up|no_tool_calls",
  "rounds": [
    {
      "round_idx": 0,
      "prompt_tokens": 0,
      "completion_tokens": 0,
      "thinking": "...",
      "raw_response": "...",
      "tool_calls": [{"name": "...", "args": {...}, "result": "..."}],
      "parse_failures": [],            # <tool_call>-shaped blocks that didn't yield a callable
      "program_after": "...",
      "errors_after": [...]
    }
  ],
  "final_program": "..."
}
```

`prompt_tokens` and `completion_tokens` are recorded per round so we
can compare per-puzzle agent cost vs. one-shot refinement spend later.

## Verification

Qualitative-first; read transcripts before counting anything.

- Unit tests for each tool, especially `edit_lines` with overlapping
  and back-to-front batches.
- Tool-call parser tested against a handful of real captured Nemotron
  responses *before* it goes in the loop.
- **Phase 1 — qualitative pass:** pick 5–10 broken programs from the
  most recent audit run (`20260426_111254`), run the agent batched in
  one go, read the transcripts by hand. Judge: did the model batch
  tool calls? did the recipes help? where did it spin? did any
  `<tool_call>` blocks fail to parse? Adjust prompt and tool set.
- **Phase 2 — small empirical pass (only after phase 1 looks sane):**
  run on the full recent-runs slice (~170 broken programs) and record
  per puzzle: `puzzle_id, n_rounds, n_tool_calls, mean_calls_per_turn,
  total_prompt_tokens, total_completion_tokens, fixed, halt_reason,
  error_count_before, error_count_after`.
- Headline metric: **fix rate** (% of broken programs that ground
  cleanly after the agent halts).
- Secondary metrics:
  - **mean tool calls per turn**. If this stays near 1.0 after
    prompt-engineering, the batching hypothesis failed.
  - **total tokens per puzzle** vs. a one-shot refinement attempt on
    the same puzzle, so we can answer "is this cheaper than just
    re-prompting from scratch?"

## Stop conditions for the experiment itself

- If after 2 prompt-engineering rounds Nemotron averages < 1.5 tool
  calls per turn, revisit whether to invest further in batching or
  switch to a single-turn agent that does best-effort full rewrite.
- If fix rate < 10%, the agent isn't earning its complexity vs. just
  letting refinement re-prompt the model from scratch — stop.
