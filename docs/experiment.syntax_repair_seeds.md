# Syntax Repair Agent — Multi-Seed + Thinking Toggle

**Date:** 2026-04-27
**Branch:** `feature/baseline`
**Predecessor:** `docs/experiment.syntax_repair_agent_results.md` (single-seed baseline of 26.7%)

---

## Question

Two follow-ups to the original 26.7% fix-rate result:

1. **Was the 26.7% a statistical anomaly?** Repeat the experiment across 5 different seeds (each sampling 20 of the 60 broken programs). Is the agent consistently above the deterministic-rewriter ~1% baseline?
2. **Does Nemotron's `<think>` reasoning matter?** Repeat the same 5-seed experiment with thinking disabled (`enable_thinking=False` in the chat template), and quantify the cost/quality tradeoff.

Same audit pool as the original measurement (`20260426_123318`, 60 broken programs).
Per-seed sampling is deterministic: `random.Random(seed).sample(pool, 20)`.

---

## Setup changes

- `src/config/config_agent.py` — `THINKING` now reads `AGENT_THINKING` env var (defaults to `true`), so a single SLURM submission can flip the mode without touching the source.
- `scripts/run_syntax_repair.py` — added `--run-name`, `--all-audits`, and seed-driven puzzle sampling. Transcripts now land in `results/syntax_repair_runs/<run_name>/` along with a `_run_meta.json` recording the run's params.
- `run_syntax_repair.job` — params come from environment variables (`SEED`, `NUM`, `RUN_NAME`, `AUDIT_RUN`, `ALL_AUDITS`, `AGENT_THINKING`).
- `scripts/launch_syntax_repair_runs.sh` — submits 5 jobs in parallel (`think` or `nothink`), one SLURM job per seed.
- `src/agent/syntax_repair_agent.py` — bumped `max_tokens_override` from **16384 → 32768**. The 16K cap was too tight on the all-audits pool: hard puzzles burned every token inside `<think>`, leaving an empty assistant response and wrongly triggering `no_tool_calls` halts.

### Bug found and fixed during this experiment

The **first** thinking-on batch ran with `--all-audits` against a 1116-program pool and the original 16K token cap. Result was 13/100 = 13% — much worse than baseline. Investigation showed:

- 14 / 190 rounds hit the 16384-token cap.
- 10 of those produced an *empty* assistant response (the model spent the entire budget inside one giant `<think>` block).
- Parse-failure rate also rose 3-4×, suggesting the wider audit pool included structurally harder programs.

Fix: raise the cap to 32768 and pin the experiment to `audit_run=20260426_123318` so the comparison is apples-to-apples with the published baseline. Buggy artifacts archived under `results/syntax_repair_runs/_archived_buggy_allaudits_16ktok/`.

---

## Results

### Thinking ON (5 seeds × 20 puzzles each)

| Seed | Fixed | no_progress | no_tool_calls | Rounds | Avg completion tok/round |
|------|-------|-------------|---------------|--------|--------------------------|
| 11   | 3/20 (15%)  | 13 | 4 | 42 | 6620 |
| 23   | 3/20 (15%)  | 11 | 6 | 41 | 6822 |
| 47   | 5/20 (25%)  | 10 | 5 | 40 | 4831 |
| 91   | 2/20 (10%)  | 10 | 7 | 42 | 7375 |
| 137  | 7/20 (35%)  | 10 | 3 | 37 | 6488 |
| **Total** | **20/100 = 20.0%** | 54 | 25 | 202 | 6440 |

- Per-run mean fix rate: **20.0%**, sample stdev **10.0 pp**.
- Range across seeds: 10% – 35%.
- Each individual run's binomial 95% CI (n=20) is roughly ±20pp, so every seed is consistent with the published 26.7% baseline.
- Pooled n=100 gives a 95% CI of ±8pp, so 20.0% is statistically distinguishable from 26.7% but only mildly. The most likely explanation is LLM-seed variance amplified by the small sample (5 of 60 puzzles overlap across most seed pairs).
- Truncations: 6 / 202 rounds. Empty responses: 4 / 202. The 32K cap is the right size for thinking mode.

**Conclusion on Q1:** the agent's improvement over the deterministic rewriter (~1% on 5/409) is robust and reproducible. Worst-case seed (10%) is still 10× the rewriter; best case (35%) is 35×. The 26.7% headline was *not* a statistical anomaly — it sits one standard deviation above the multi-seed mean.

### Thinking OFF (same 5 seeds, same puzzles)

| Seed | Fixed | no_progress | no_tool_calls | Parse fails | Truncated | Avg completion tok/round |
|------|-------|-------------|---------------|-------------|-----------|--------------------------|
| 11   | 0/20 (0%)   | 12 | 8 | 24 | 1 | 1282 |
| 23   | 0/20 (0%)   | 16 | 4 | 34 | 4 | 3947 |
| 47   | 3/20 (15%)  | 15 | 2 | 24 | 3 | 3017 |
| 91   | 1/20 (5%)   | 15 | 4 | 13 | 5 | 4410 |
| 137  | 2/20 (10%)  | 18 | 0 |  1 | 1 | 1274 |
| **Total** | **6/100 = 6.0%** | 76 | 18 | 96 | 14 | 2794 |

- Per-run mean: **6.0%**, sample stdev **6.5 pp**.
- 2 of 5 seeds scored zero. The remaining seeds (5–15%) are still above the deterministic rewriter, but the variance is now in the noise floor.
- Parse-failure count is ~10× higher than thinking-on (96 vs 32 across all rounds), and 14 / 216 rounds hit the 32K cap. Without an internal scratchpad, Nemotron emits more malformed tool wrappers and longer rambling completions.
- Avg completion tokens/round: 2794 (vs 6440 with thinking) — a **57% drop** in completion tokens.

### Wall-clock comparison

| Mode | Seeds (mm:ss) | Mean |
|------|---------------|------|
| think   | 09:42, 10:31, 05:45, 10:01, 06:44 | ~08:33 |
| nothink | 03:03, 08:29, 08:01, 15:28, 06:07 | ~08:13 |

Thinking-OFF is **not meaningfully faster** in wall-clock terms. The completion-token savings are absorbed by extra rounds (216 vs 202), more truncations, and the model occasionally generating long unbounded rule expansions when no `<think>` block is there to pre-plan the edit.

---

## Verdict

| Mode | Fix rate | Stdev | Rounds | Tokens/round | Wall clock |
|------|----------|-------|--------|--------------|------------|
| think    | **20.0%** | 10.0pp | 202 | 6440 | ~8:33 |
| nothink  | 6.0%   | 6.5pp  | 216 | 2794 | ~8:13 |

- **Reproducibility (Q1):** confirmed. The agent consistently outperforms the deterministic rewriter across seeds.
- **Disable thinking (Q2):** **not worth it.** A 3.3× drop in fix rate for a 57% token reduction *and* roughly equal wall clock. The bottleneck is GPU-resident model execution, not generated-token volume — so the savings don't translate.

### Why nothink struggles

- Without `<think>`, Nemotron commits to a tool-call format faster but with weaker self-correction → parse-failure rate jumps.
- Edit decisions are made directly from the prompt, so it tends to attempt larger, riskier edits in one shot (more `no_progress` halts).
- The model occasionally enters a long rambling completion that hits the 32K cap, which mirrors what thinking-on did with the 16K cap pre-fix.

If we wanted nothink to be competitive, the prompt would need much more explicit step-by-step structure (or an extra "plan the edit" tool round) — at which point we're paying the rounds we saved.

---

## Files

- Transcripts: `results/syntax_repair_runs/seed{11,23,47,91,137}_{think,nothink}/`
- Per-run config: `results/syntax_repair_runs/<run_name>/_run_meta.json`
- SLURM logs: `slurm_logs/seed*_{think,nothink}_*.out`
- Buggy first attempt: `results/syntax_repair_runs/_archived_buggy_allaudits_16ktok/`

---

## Next steps

- **Ramp from per-seed 20 to per-seed 60** (full pool, no subsampling) for tighter CI. Cost: ~3× wall clock.
- **Mid-ground prompt** — keep thinking enabled but cap it explicitly (`<think>` budget tokenizer-side) and measure whether short thinking is enough.
- **Prompt-structure tweaks for nothink** — add an explicit "plan first, then tool-call" stanza to the system prompt; rerun nothink to see if structured prompting closes the gap.
