---
name: Better batching
status: closed
---

# Plan: Maximize Batching in asp-arc Pipeline

## Context

With 4M tokens of KV-cache on H100 and MAX_MODEL_LEN=220K, vLLM can schedule ~18 requests concurrently. The current pipeline uses vLLM's **synchronous** `LLM.chat()` — a global blocking call — so only one batch is ever in-flight at a time. The GPU sits idle during serial Clingo verification between each refinement round (up to 30s × N_puzzles × N_examples per round).

The current timeline looks like:
```
[LLM batch N puzzles] → [Clingo p1] → [Clingo p2] → ... → [Clingo pN] → [LLM batch N puzzles] → ...
GPU:        BUSY                           IDLE                                     BUSY
```

Goal: keep the GPU busy by having multiple puzzles' LLM requests in-flight simultaneously, overlapping with Clingo.

---

## Three Changes (ordered by impact)
- Each change must be introduced ONE-BY-ONE and tested, verified, evaluated, before the next change can be introduced and repeat the same cycle.
- To evaluate a change: analyze the logs, outputs, LLM's inputs/thinking/outputs from the cache files and/or results. Simply make sure that everything is as it should be.

### Change 1 — Flatten Stage 1 candidate generation (trivial)

**File**: `main.py:210–220`

Currently: 6 sequential `gen_response_batch()` calls of size N (one per strategy).  
After: one `gen_response_batch()` call of size 6×N (all strategies × all puzzles).

```python
# Before
for cand_idx in range(N_CANDIDATES):
    strategy_name, suffix = CAND_STRATEGIES[cand_idx]
    cand_replaces = [{"<EXAMPLES>": fe + suffix} for fe in formatted_examples]
    cand_gen_results = pipeline.gen_response_batch("single_step", cand_replaces)
    all_cand_results.append(cand_gen_results)
    all_cand_programs.append([extract_code_blocks(resp) for _, resp in cand_gen_results])

# After
all_replaces = [
    {"<EXAMPLES>": formatted_examples[pi] + suffix}
    for _, suffix in CAND_STRATEGIES[:N_CANDIDATES]
    for pi in range(n)
]
flat_results = pipeline.gen_response_batch("single_step", all_replaces)
all_cand_results = [flat_results[c * n:(c + 1) * n] for c in range(N_CANDIDATES)]
all_cand_programs = [
    [extract_code_blocks(resp) for _, resp in row] for row in all_cand_results
]
```

Eliminates 5 idle gaps between the 6 Stage 1 LLM calls with zero structural changes elsewhere.

---

### Change 2 — `AsyncNemotronEngine` using vLLM's `AsyncLLMEngine`

**File**: `nemotron_engine.py`

Add `AsyncNemotronEngine` alongside the existing `NemotronEngine`. The async engine exposes a `generate_one(messages)` coroutine. When multiple coroutines concurrently `await generate_one()`, vLLM's scheduler batches all pending requests together automatically.

```python
class AsyncNemotronEngine:
    def __init__(self):
        from vllm.engine.async_llm_engine import AsyncLLMEngine
        from vllm import AsyncEngineArgs, SamplingParams
        from transformers import AutoTokenizer

        engine_args = AsyncEngineArgs(
            model=MODEL_PATH,
            trust_remote_code=True,
            mamba_ssm_cache_dtype="float32",
            kv_cache_dtype="fp8",
            max_model_len=MAX_MODEL_LEN,
            tensor_parallel_size=1,
            seed=SEED,
        )
        self.engine = AsyncLLMEngine.from_engine_args(engine_args)
        # AsyncLLMEngine has no .chat(); apply chat template manually
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
        self.sampling_params = SamplingParams(
            temperature=TEMPERATURE, top_p=TOP_P, top_k=TOP_K, max_tokens=MAX_MODEL_LEN
        )

    async def generate_one(self, messages):
        """One LLM call; concurrent calls are batched by vLLM automatically."""
        import uuid
        prompt = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        request_id = str(uuid.uuid4())
        final = None
        async for output in self.engine.generate(prompt, self.sampling_params, request_id):
            final = output
        return _split_thinking(final.outputs[0].text)
```

---

### Change 3 — Per-puzzle async refinement loop

**File**: `main.py` — replace the `for attempt in range(MAX_ATTEMPTS)` block (lines 532–626)

Each puzzle becomes an independent `asyncio` coroutine that loops through its own refinement attempts. All puzzles run concurrently via `asyncio.gather()`. Clingo runs in a `ThreadPoolExecutor` so it never blocks the event loop.

New timeline:
```
puzzle_1: [LLM req →] await → [Clingo thread] → [LLM req →] await → ...
puzzle_2:    [LLM req →] await → [Clingo thread] → [LLM req →] ...
puzzle_3:       [LLM req →] await → ...
                ↑ vLLM batches all in-flight requests together
GPU: ████████████████████████████████ (continuous)
```

Key structure (replaces lines 532–631 in `main.py`):

```python
async def _run_refinement_async(
    puzzles, async_engine, pipeline, formatted_examples,
    records, histories, system_prompt, instruction
):
    loop = asyncio.get_event_loop()
    # ThreadPoolExecutor for Clingo: N_puzzles × (1 + N_train_examples) concurrent calls
    executor = ThreadPoolExecutor(max_workers=min(64, len(puzzles) * 5))

    async def _refine_one(i):
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if records[i]["all_train_correct"]:
                return

            prompt = _build_reattempt_prompt(
                system_prompt, instruction, formatted_examples[i], histories[i]
            )
            # await: yields control, letting other puzzle tasks submit their requests
            thinking, response = await async_engine.generate_one(
                [{"role": "user", "content": prompt}]
            )
            new_program = extract_code_blocks(response)

            # Pure Python — no blocking
            new_program, _ = quick_syntax_fix(new_program)

            # Clingo in thread pool — non-blocking
            syntax_err = await loop.run_in_executor(
                executor, _check_syntax_fn, new_program, pipeline
            )
            if syntax_err:
                new_program = await _async_rewrite_syntax_fix(
                    new_program, syntax_err, async_engine, pipeline, loop, executor
                )

            new_train_results = await loop.run_in_executor(
                executor, verify_on_training_examples,
                new_program, puzzles[i]["train"], pipeline
            )

            is_correct = all_correct(new_train_results)
            records[i]["refinements"].append({
                "attempt": attempt,
                "prompt": prompt,
                "thinking": thinking,
                "response": response,
                "program": new_program,
                "train_verifications": new_train_results,
                "all_train_correct": is_correct,
            })

            if is_correct:
                records[i]["all_train_correct"] = True
                records[i]["final_correct"] = True
                return
            else:
                histories[i].append((new_program, build_train_feedback(new_train_results)))

    await asyncio.gather(*[_refine_one(i) for i in range(len(puzzles))])
```

The helper `_async_rewrite_syntax_fix()` is the async mirror of `rewrite_syntax_fix()` in `agent.py` — same logic but uses `await async_engine.generate_one()` instead of `engine.generate_batch([messages])[0]`. Add it to `agent.py` (or inline in `main.py`).

The existing synchronous `_run_pipeline()` calls this async function at the refinement stage via:
```python
asyncio.run(_run_refinement_async(...))
```

---

## Additional Detail: Cache Thread Safety

**File**: `pipeline.py`

The cache dict (`self.cache[kind]`) is written from multiple concurrent coroutines in the async refinement loop. Dict item assignment is GIL-protected and safe. But `save_cache()` (file I/O) can interleave. Add an `asyncio.Lock` per cache kind (or a single lock for all), acquired before writing and saving:

```python
self._cache_lock = asyncio.Lock()

async def _save_cache_async(self, kind, prompt, thinking, resp):
    async with self._cache_lock:
        self.cache[kind][prompt] = {"response": resp, "thinking": thinking}
        self.save_cache(kind)
```

---

## Files Changed

| File | Change |
|------|--------|
| `nemotron_engine.py` | Add `AsyncNemotronEngine` |
| `agent.py` | Add `async_rewrite_syntax_fix()` (async mirror of `rewrite_syntax_fix`) |
| `pipeline.py` | Add `_get_async_engine()`, `asyncio.Lock` on `save_cache` for async path |
| `main.py` | (1) Flatten Stage 1 batch; (2) Replace refinement loop with `asyncio.gather()` per-puzzle tasks + `ThreadPoolExecutor` for Clingo |

## What Does NOT Change

- `run_syntax_agent()` in `agent.py`: the multi-turn tool loop is inherently sequential per puzzle. Stage 2 already runs one puzzle at a time and is not the bottleneck.
- `eval.py`: `verify_on_training_examples()` is unchanged — it's called from the thread pool executor, so multiple instances run concurrently across puzzles.
- Result schema in `records`: same JSON structure, same fields.
- The synchronous `NemotronEngine`: kept for Stage 2 syntax agent.

## Verification

```bash
python main.py --num 5
```

Check:
1. `results/<run_id>.json` has same schema and plausible solve rates
2. Log output shows concurrent Clingo calls (interleaved puzzle IDs in logs)
3. Wall-clock time for the refinement phase is substantially shorter than before
4. No `asyncio` exceptions or cache corruption in the log
