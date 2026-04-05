# Batching Experiments — Run Tracking

This document maps SLURM job IDs to codebase stages for the `feature/agentic-nemotron`
batching improvements described in `issues/better_batching.md`.

---

## Baseline (pre-batching)

No dedicated baseline run recorded for this series. Prior runs on the same branch
(6-candidate generation, sequential Stage 1 calls) serve as informal baseline.

---

## Change 1 — Flat Stage 1 batch

**Commit:** see git log for `"Change 1: flat Stage 1 batch"`  
**What changed:** `main.py` — replaced 6 sequential `gen_response_batch()` calls with
one flat call of size `N_CANDIDATES × n` (6 × 3 = 18 requests in one vLLM batch for
the default 3-puzzle run). Eliminates 5 inter-candidate idle GPU gaps in Stage 1.

| SLURM Job ID | Puzzles | Notes |
|---|---|---|
| TBD — submitted after commit | 8e1813be 834ec97d 469497ad | First run with flat batch |

**Verification checklist:**
- [ ] `results/<run_id>.json` has same schema, plausible programs
- [ ] 18 cache entries appear in `caches/vllm_nemotron-cascade-2_single_step.json`
  (6 candidates × 3 puzzles = 18 distinct prompts)
- [ ] No index errors in reshaping (check `all_cand_results[c][i]` matches original)
- [ ] Solve rate comparable to baseline

---

## Change 2 — AsyncNemotronEngine

**Commit:** see git log for `"Change 2: AsyncNemotronEngine"`  
**What changed:** `nemotron_engine.py` — added `AsyncNemotronEngine` using
`AsyncLLMEngine`. `pipeline.py` — added `_get_async_engine()` and `asyncio.Lock`
for thread-safe async cache writes.

| SLURM Job ID | Puzzles | Notes |
|---|---|---|
| — | — | Not independently testable without Change 3 |

---

## Change 3 — Per-puzzle async refinement loop

**Commit:** see git log for `"Change 3: async refinement loop"`  
**What changed:** `main.py` — replaced sequential `for attempt` refinement loop with
`asyncio.gather()` per-puzzle coroutines; Clingo runs in `ThreadPoolExecutor`.
`agent.py` — added `async_rewrite_syntax_fix()`.

| SLURM Job ID | Puzzles | Notes |
|---|---|---|
| TBD — submitted after commit | 8e1813be 834ec97d 469497ad | First run with async refinement |

**Verification checklist:**
- [ ] `results/<run_id>.json` has same schema and `refinements` list per puzzle
- [ ] Log shows interleaved puzzle IDs during refinement (concurrent Clingo calls)
- [ ] Refinement wall-clock time substantially shorter than Change 1 run
- [ ] No `asyncio` exceptions or cache corruption in logs
- [ ] Solve rate comparable or better

---

## How to inspect a run

```bash
# View SLURM output
cat outputs/slurm_output_<JOB_ID>.out

# Inspect results via Streamlit
streamlit run streamlit-interface/app.py
```

Or use the `/inspect-run` skill in Claude Code:
```
/inspect-run <run_id>
```
