# Syntax Repair Agent — Qualitative Analysis

**Date:** 2026-04-27
**Branch:** `feature/baseline`
**Predecessor:** `docs/experiment.syntax_repair_seeds.md` (quantitative multi-seed results)

---

## Overview

Following the multi-seed experiment (20.0% fix rate with thinking ON, 6.0% with thinking OFF), this document provides a qualitative analysis of *what the agent is actually doing* — where it succeeds, where it fails, and why. It draws on a broad sweep of all 418 rounds across 10 run directories (`seed{11,23,47,91,137}_{think,nothink}`) plus deep reads of 4 selected transcripts.

---

## Tool-Call Syntax Failure Rates

The first quantitative finding from the qualitative investigation: formatting failures are common and measurable.

**24.6% of all rounds** had a formatting failure — either a logged parse error or a non-empty response that yielded no extractable tool calls.

| Mode | Rounds | Failure rounds | Rate |
|------|--------|----------------|------|
| think | 202 | 31 | **15.3%** |
| nothink | 216 | 72 | **33.3%** |
| combined | 418 | 103 | **24.6%** |

The correct format (as specified in the system prompt) is:

```
<tool_call>
edit_lines
{"start": 43, "end": 48, "new_text": "..."}
</tool_call>
```

The parser has three fallback strategies (standard `<tool_call>` blocks → bare `<edit_lines>` tags → `<function=function>` prefix). Despite these, 103 rounds fail to produce any tool call.

### Error taxonomy (142 individual failure events)

| Category | think | nothink | total | % |
|----------|-------|---------|-------|---|
| Double-nested `<tool_call>` tag | 3 | 54 | 57 | 40.1% |
| XML tag used as tool name | 17 | 2 | 19 | 13.4% |
| Unparseable / fallback-parser errors | 7 | 6 | 13 | 9.2% |
| Truncated JSON args (budget cut-off) | 0 | 12 | 12 | 8.5% |
| Silent XML tag (no parse failure logged) | 5 | 7 | 12 | 8.5% |
| Triple-nested `<tool_call>` | 0 | 11 | 11 | 7.7% |
| Other / miscellaneous | 5 | 2 | 7 | 4.9% |

**Double-nested `<tool_call>` (40.1%, nothink-dominated):** The model emits an extra `<tool_call>` opener inside the block. The parser's non-greedy regex grabs up to the first `</tool_call>`, sees inner `<tool_call>` as the tool name, and fails to parse `edit_lines\n{...}` as JSON args.

```
<tool_call>
<tool_call>        ← extra tag
edit_lines
{"start": 40, ...}
</tool_call>
```

**XML tag as tool name (13.4%, think-dominated):** The model wraps the tool name in its own tags rather than writing the bare name, or nests a different XML tag inside:

```
<tool_call>
<tool>read_program</tool>   ← wrong
{}
</tool_call>

<tool_call>
<edit>                      ← wrong
<read_program>{"start_line": 31, "end_line": 45}
</tool_call>
```

This is predominantly a think-mode failure (17 of 19 events) — the model confuses its general XML templating with tool-call format.

**Truncated JSON args (8.5%, nothink-only):** The model runs out of token budget mid-argument:

```
<tool_call>
edit_lines
{"start":15,"end":15,"new_text":"zero_cell_rhs(R, C, Color, N) :- n_rows(N), (N - #count{ R2 : input(
</tool_call>
```

Never seen in think mode — the `<think>` block absorbs planning, leaving short focused output.

**Key implication:** These are almost entirely *format* failures, not semantic failures. The model knew what tool to call and what the fix should be, but mangled the wrapper. A more robust parser — one that tolerates a leading `<tool_call>` line inside a block, or accepts `<edit_lines>...</edit_lines>` without requiring `</tool_call>` as closing — would likely recover a substantial fraction.

---

## Halt Reason Distribution (think-mode, all 5 seeds)

| Halt reason | Count | % |
|-------------|-------|---|
| fixed | 20 | 20.0% |
| no_progress | 54 | 54.0% |
| no_tool_calls | 25 | 25.0% |
| gave_up | 1 | 1.0% |

`no_progress` (54%) is the dominant failure mode: the agent makes edits but the Clingo error set is identical across rounds. `no_tool_calls` (25%) is the formatting failure path. The single `gave_up` is discussed below.

---

## Qualitative Patterns

### What succeeds

- **Localized syntax errors with clear structure** — aggregate variables missing grounding, wrong operator (`=` vs. `:-`). The agent converges when it can test a hypothesis with `clingo_check`, get a different error, and narrow in.
- **Iterative refinement** — the agent often doesn't nail the fix on the first try, but each round's Clingo feedback moves it closer. 3-round solutions are common.

### What fails

**1. Formatting collapse (no_tool_calls)**

The agent reasons correctly, plans a correct fix, then emits wrong tag structure. Seen varieties: missing `<tool_call>` openers, `tool_name: edit_lines` prefix, `<edit_lines>` vs `<tool_call>\nedit_lines`. The variety of breakage patterns is wide.

**2. Semantic confusion (no_progress)**

The agent patches symptoms rather than root causes, or fixes one rule without updating the dependent rules that share the same unsafe variable. Most common with unsafe variable errors, where fixing one rule can introduce unsafeness elsewhere.

---

## Four Case Studies

### Case 1: The 3-Round Success — `57aa92db` (seed137_think)

**Problem:** Aggregate-in-head form instead of proper rule:
```prolog
zeroCount(R, D) = #count{ Z : zero(R, Z), Z < D }.   % syntax error
```

**Round 0:** Agent misdiagnoses the problem (suspects comma placement, not the `=`-form) but makes a plausible edit — converts to parameterized predicate. Introduces unsafe variables.

Round 0 thinking excerpt:
> *"Clingo expects braces around aggregate arguments but there is a comma after Z inside the aggregate… In ASP aggregates, the aggregate body must be a collection of atoms separated by commas? Actually syntax for `#count{...:...}` expects a collection of atoms each possibly with a condition after colon? Actually typical: `#count{ R : input(R, _, _) }`. […] But Clingo error says unexpected comma…"*

**Round 1:** Recognizes it needs `col(D)` grounding but still misses `row(R)`.

**Round 2:** Adds both `row(R)` and `col(D)` grounding:
```prolog
zeroCount(R, D, NZ) :- row(R), col(D), NZ = #count{ Z : zero(R, Z), Z < D }.
activeCount(R, D, NA) :- row(R), col(D), NA = #count{ D2 : active(R, D2), D2 <= D }.
output(R, C, Color) :- active(R, D, Color), zeroCount(R, D, NZ), activeCount(R, D, NA), C = NZ + NA - 2, col(C).
```

Clingo: *"No errors (grounding succeeded)."*

**Insight:** The agent succeeds by grinding through Clingo feedback, not by immediately understanding the problem. It never had a crisp diagnosis — it just kept tightening grounding constraints.

---

### Case 2: The Give-Up — `39e1d7f9` (seed91_think)

The only `gave_up` call across all 100 think-mode transcripts.

**Problem:** The program uses uppercase predicate names (`Rmin/1`, `Rmax/1`, `Cmin/1`, `Cmax/1`). In Clingo, uppercase identifiers are *variables*, not predicates. So `Rmin(N) :- ...` is parsed as variable `Rmin` followed by invalid `(N)`. Fix requires a systematic rename to lowercase throughout.

**Round 0:** Agent reads the program. Does not attempt any edit. Errors unchanged.

**Round 1 thinking** (7,966 tokens): Spends enormous effort trying to explain why `Rmin(N)` would be invalid, going in circles:
> *"Actually the rule head `Rmin(N)` is a predicate with arguments Rmin and N. The syntax is `Rmin(N)` is allowed; but the colon and hyphen may cause confusion? Wait the error 'unexpected (' indicates that after reading `Rmin(N) :- N = #min{R : inner(R,_,_) }.` the parser sees '(' after reading 'Rmin', then sees…"*

After nearly 8,000 tokens, the agent calls:
```json
{"name": "give_up", "args": {"reason": "The program contains multiple syntax errors around parentheses in rule heads that are not easily fixable with minimal edits; further rewrites would require extensive changes beyond incremental fixing."}}
```

The diagnosis is wrong (it blames parentheses, not uppercase naming) but the conclusion is accidentally correct: the fix requires a systematic renaming pass that exceeds incremental repair. The agent can feel the intractability without seeing it clearly.

**`errors_after` after give_up is `[]`** — the orchestrator doesn't run Clingo following a give_up; this is an artifact of the transcript format.

---

### Case 3: The Thinking Flood — `1e0a9b12` (seed137_think)

The most striking case: excellent reasoning, complete formatting failure at the last step.

**Problem:** Unsafe variables in:
```prolog
rank(R, C, Rank) :- Rank = #count{R2 : nonzero(R2, C, Color2), Color2 != 0, R2 > R}.
```
`R`, `C`, and `Rank` are all unsafe — none appear in a positive body literal outside the aggregate.

**Round 0 thinking:** 78,467 characters. The agent immediately diagnoses the problem correctly:
> *"The notes say C is unsafe, R is unsafe, Rank is unsafe. In Clingo, variables that appear only in aggregate condition (or in negation) need to be grounded elsewhere in the body. The rank rule should bind R, C, Rank positively before using them in aggregate condition. R and C appear only inside aggregate condition and not elsewhere positively. In the body we have nothing else besides `Rank = ...` So R and C are not grounded."*

The thinking works through a detailed fix (introduce `countBelow` predicate with `row(R)`, `col(C)` grounding), considers and dismisses alternatives, double-checks line counts, worries about period terminators. The fix plan is correct.

**Raw response:**
```
edit_lines
{"start": 31, ...}
</tool_call>
edit_lines
{"start": 17, ...}
</tool_call>
clingo_check
{}
</tool_call>
```

Every call is missing its opening `<tool_call>` tag. The parser finds nothing. No parse failures are even logged — there is simply nothing to parse. Result: `no_tool_calls`, program unchanged.

**Insight:** 78K characters of correct reasoning produced zero tool calls. The token budget for thinking mode doesn't reduce what's available for the response, but something in the generation of the actual response phase broke down after an extremely long think.

---

### Case 4: The Cascade Failure — `54d9e175` (4 consecutive attempts, seed137_think)

A puzzle that receives the maximum 4 attempts, with a different structural failure each time — and the program gets *worse*.

**Attempt 0 (`54d9e175.json`): `no_progress`**
Agent reads program with `choice 1 { Sep1 : all5Col(Sep1) } 1 :- Sep1 = 0.` (unexpected `<NUMBER>` error). In Round 1 it generates plausible edits but prefixes every tool name with `tool_name:`:
```
<tool_call>
tool_name: edit_lines
{"start": 14, ...}
</tool_call>
```
Orchestrator: *"Unknown tool 'tool_name: edit_lines'. Available: read_program, edit_lines, clingo_check, give_up."* All 4 calls fail identically. Errors unchanged.

**Attempt 1 (`54d9e175_1.json`): `no_progress`**
New program version, `#max` aggregate errors. Agent plans a batch of edits for lines 31–37 but gets a runtime error back: *"overlapping edit_lines ranges in batch: lines 31–37 and lines 31–37."* Round 1 thinking:
> *"We got an error: overlapping edit_lines ranges in batch: lines 31–37 and lines 31–37. Wait we have only one edit_lines call, but why does it think overlapping?"*
Two rounds burn reading the program without making progress.

**Attempt 2 (`54d9e175_2.json`): `no_tool_calls`**
58,610 characters of thinking, a complete fix plan. Raw response uses angle-bracket wrapping for the tool name:
```
<edit_lines>
{"start": 69, "end": 69, "new_text": "..."}
</tool_call>
```
Opening tag is `<edit_lines>`, closing tag is `</tool_call>`. Neither the standard regex nor the bare-tool fallback matches. Zero calls parsed.

**Attempt 3 (`54d9e175_3.json`): `no_progress`**
By this point the program has been mutated by prior failed edits. New initial error: `syntax error, unexpected <IDENTIFIER>` on line 2 (a standard `#count` rule). Round 1 makes 5 edits, one of which replaces line 1 with `asp.` — syntactically invalid Clingo. Unsafe variable errors persist through Round 2 and the attempt ends.

**Insight:** Each failed attempt leaves the program slightly worse. By Attempt 3, prior mutations have introduced new errors, and the agent is trying to fix a program that barely resembles the original. The cascade shows how state accumulates badly: there is no rollback between attempts.

---

## Summary

| Question | Finding |
|----------|---------|
| How often does format failure occur? | 24.6% of rounds (15.3% think, 33.3% nothink) |
| What's the dominant failure mode? | Double-nested `<tool_call>` tag (40% of events), mostly nothink |
| Are these semantic or format failures? | Almost entirely format — the model knew what to call |
| Where does the agent succeed? | Localized errors, iterative Clingo feedback loop |
| Where does it fail semantically? | Unsafe variables across dependent rules, requires whole-program reasoning |
| Does thinking volume predict success? | No — 78K-char think trace produced zero tool calls |
| Does give_up fire usefully? | Once, on a case that genuinely needed a systematic rename (though diagnosis was wrong) |
| Does program state accumulate badly? | Yes — failed attempts mutate the program, making subsequent attempts harder |

### Implications for next steps

- **Parser hardening** would recover a meaningful fraction of the 24.6% format-failure rate at no model cost. Specifically: tolerate a leading `<tool_call>` line inside a block (strip it), and accept `<edit_lines>...</edit_lines>` as a fallback even when `</tool_call>` is the closing tag.
- **Rollback between attempts** would prevent the cascade failure pattern from compounding. Starting each retry from the original broken program rather than the mutated state of the prior attempt.
- **Unsafe variable failures** are the hard semantic problem. They require the agent to reason across multiple rules simultaneously. Prompt additions that explicitly teach variable grounding strategy (vs. the current examples which are simpler) might help.
