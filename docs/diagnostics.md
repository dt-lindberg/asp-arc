# OOM diagnostics: how to read the logs

Two batch runs (`22606709`, `22609703`) ended in `OUT_OF_MEMORY`. They have
different signatures and we don't yet know which is which:

- **22606709** (no client-side cap): `MaxRSS=186 GB` — straightforward
  cgroup-limit OOM, ~5.5 h to fail. The unbounded `asyncio.gather` over the
  whole sample was the obvious cause; a worker-pool cap was added in
  `31267b7`.
- **22609703** (cap=100): `MaxRSS=93 GB` — only ~52 % of the 180 GB cgroup
  limit, and yet OOM-killed at the ~1.4 h mark. The cap is working
  (in-flight steady at 99–100), so the second OOM is a different mechanism.
  Either Slurm's 30 s sampling missed a transient peak, or the OOM trigger
  is something other than gradual RSS growth.

The diagnostics added in `src/utils/diagnostics.py` and called from
`src/batch_pipeline.py` are **pure observation** — no behavior changes. They
exist to localize the next OOM enough to decide on a fix.

---

## What is logged

### A. Periodic sampler (every 10 s)

A single line at `INFO`:

```
DIAG cgroup_peak=82.3GB self_rss=14.1GB self_hwm=14.4GB
     vllm_rss=66.0GB vllm_hwm=66.2GB engine_rss=2.1GB
     tasks=103 threads=22 gc_objs=842113
```

| Field | Source | What it measures |
|---|---|---|
| `cgroup_peak` | `/sys/fs/cgroup/<job>/memory.peak` | Kernel-tracked **monotonic watermark** of the entire cgroup (python + vLLM API server + EngineCore + any subprocesses). Cannot miss a burst between samples. |
| `self_rss` / `self_hwm` | `/proc/self/status` | Current and high-water resident memory of the **python pipeline**. `VmHWM` is the kernel watermark for this process alone. |
| `vllm_rss` / `vllm_hwm` | `/proc/<vllm_pid>/status` | Same for the **vLLM API server** (the `vllm serve` process). |
| `engine_rss` | `/proc/<engine_pid>/status` | The **EngineCore** process, which owns the GPU. Listed separately because it grows for different reasons (KV swap, prefix cache spill). |
| `tasks` | `len(asyncio.all_tasks())` | Number of live coroutines — should hover around `MAX_CONCURRENT_PUZZLES + a few`. |
| `threads` | `threading.active_count()` | Number of OS threads — Clingo spawns one daemon thread per `run_clingo` call and relies on it exiting. |
| `gc_objs` | `len(gc.get_objects())` | Total live python objects — a global sanity check on accumulation. |

A one-shot `DIAG init …` line is emitted at the start of the run so you know
which PIDs are being probed and which cgroup path was resolved.

### B. Per-call RSS deltas (only when over threshold)

Two call sites in `handle_puzzle` are wrapped with RSS-before/after reads:

- `engine.chat_async(msgs)` — the LLM round-trip.
- `validate_asp_program(asp_code, p1, p2)` — Clingo grounding/solving across
  30 grid pairs.

If self-RSS grows by more than `RSS_DELTA_LOG_THRESHOLD_GB` (default 1.0 GB)
across either call, a `WARNING` line is emitted:

```
DIAG validate rss_delta=12.3GB before=18.4GB after=30.7GB
     d511f180/2dc579da round=1 asp_chars=4127 passed=False correct=0/30
```

```
DIAG chat_async rss_delta=2.1GB before=14.0GB after=16.1GB
     bc1d5164/ff28f65a round=2 msg_chars=148003 resp_chars=8842 thinking_chars=42008
```

These deltas are noisy because RSS is process-wide and other workers are
running concurrently — a 1 GB jump on a single call can be coincidental.
**A 10 GB jump cannot.**

The puzzle name and ASP-code length are included so a suspect call can be
replayed offline against the JSONL output.

---

## Hypotheses we are testing

| H# | Hypothesis | Best evidence in the diagnostics |
|---|---|---|
| H1 | Concurrent Clingo grounding bursts on pathological generated programs | `validate` RSS-delta lines appearing close together; `cgroup_peak` jumps in the same window; `self_rss` spikes (Clingo grounder allocates inside the python process via the `clingo` extension) |
| H2 | vLLM API server prompt/buffer accumulation | `vllm_rss` (or `vllm_hwm`) climbs while `self_rss` stays flat; cgroup peak grows monotonically rather than in bursts |
| H3 | vLLM EngineCore KV / prefix cache spill | `engine_rss` climbs over time (note: GPU memory is *separate* from RSS, so prefix-cache hits on RSS only via swap-space spill) |
| H4 | OpenAI/httpx response-buffering bursts | `chat_async` RSS-delta lines fire repeatedly, especially at high `resp_chars` or `thinking_chars` |
| H5 | Coroutine / task / thread leak | `tasks` or `threads` grows without bound across samples |
| H6 | Slow Python-object accumulation (history dicts, message lists, unreleased response strings) | `gc_objs` and `self_rss` both climb gradually with no per-call deltas exceeding threshold; needs `tracemalloc` follow-up |
| H7 | Slurm misreported the cgroup limit / host pressure | `cgroup_peak` plateaus well below 180 GB at OOM time; `MemAvailable` from `/proc/meminfo` (not currently logged, follow-up) shows host squeeze |

---

## How to interpret a run

The decision tree below assumes you've reproduced an OOM and have the slurm
log open. Search for `DIAG`.

### Step 1 — does `cgroup_peak` reach the limit?

Find the last few `DIAG` lines before the OOM marker.

- **`cgroup_peak` ≈ 180 GB** → the cgroup really filled up. Continue to step 2.
- **`cgroup_peak` ≪ 180 GB** (e.g. 95 GB) at the last sample → either a
  transient burst between the last sample and the OOM, or the cgroup limit
  is lower than `seff` reports. Look at the *delta between consecutive
  samples* — a 100 GB jump in one 10 s window is the burst signature; check
  for a `validate` or `chat_async` RSS-delta line with a comparable
  magnitude in the same second.

### Step 2 — which process grew?

Compare `self_hwm` and `vllm_hwm` (and `engine_rss`) at the last sample.

- **`self_hwm` is the dominant grower** → python-side. Go to step 3.
- **`vllm_hwm` is the dominant grower** → server-side. Hypothesis H2 or H3.
  The follow-up is `/proc/<vllm_pid>/smaps_rollup` (one-shot, not currently
  logged); growth in `Anonymous` points at malloc/buffer leaks, growth in
  `Shared_Dirty` points at IPC buffers between APIServer and EngineCore.
- **Both grew comparably** → likely H1 (Clingo can affect python-side; it
  shouldn't push vLLM unless the host is also under broader pressure).

### Step 3 — burst or slow climb?

Look at the trajectory of `self_rss` across the last 30–60 minutes of
samples (a quick `awk '/DIAG / && /self_rss/ {print $1, $2, $4}'` pulls
just timestamp + self_rss).

- **Climbs smoothly** → slow accumulation; H6 most likely. Follow up with
  `tracemalloc` snapshots — that's the only way to attribute Python-object
  retention to a source line.
- **Roughly flat with sudden jumps** → burst. Check the `validate` and
  `chat_async` RSS-delta WARN lines around each jump:
  - Jumps correlated with `validate` lines, especially with low
    `passed=False correct=0/30` and large `asp_chars` → H1 confirmed.
  - Jumps correlated with `chat_async` lines, especially with large
    `msg_chars` (refinement round 2 has the largest history) → H4.
  - Jumps with neither delta line firing → the leak is elsewhere on the
    python side (the threshold may need lowering, or the allocator is
    inside an `asyncio.to_thread` that we didn't wrap).

### Step 4 — concurrency sanity

Across the run:

- **`tasks` stays ≈ `MAX_CONCURRENT_PUZZLES`** (≈ 100–105) → worker pool
  healthy.
- **`tasks` climbs above the cap** → a coroutine isn't completing somewhere
  (await blocked, exception swallowed, queue.task_done not called).
- **`threads` grows monotonically** → Clingo daemon threads from
  `clingo_runner.py:_do_ground` aren't exiting (consistent with timed-out
  groundings whose threads are still alive). Even if it doesn't OOM us, this
  is a real leak.
- **`gc_objs` grows linearly with cumulative puzzles, not with current
  in-flight puzzles** → live references are surviving past their owning
  coroutine; H6.

---

## Worked example

Suppose the next OOM produces this tail:

```
DIAG cgroup_peak=82.1GB self_rss=14.0GB vllm_rss=66.0GB engine_rss=2.1GB tasks=101 threads=23 gc_objs=910k
DIAG cgroup_peak=82.4GB self_rss=14.3GB vllm_rss=66.0GB engine_rss=2.1GB tasks=103 threads=24 gc_objs=920k
DIAG validate rss_delta=14.8GB before=14.4GB after=29.2GB d511f180/2dc579da round=1 asp_chars=5012 passed=False correct=0/30
DIAG validate rss_delta=11.2GB before=29.0GB after=40.2GB bc1d5164/ff28f65a round=1 asp_chars=3811 passed=False correct=0/30
[OOM]
```

Reading:

- `cgroup_peak` was at 82 GB just before — well under the 180 GB limit, so
  the OOM was a **burst**, not gradual exhaustion.
- Two `validate` RSS-delta WARN lines fired in the same window with
  combined growth of ~26 GB on top of an already 80 GB resident set.
- `self_rss` and `validate before/after` grew together, pinning the burst
  to the python side.
- The puzzles `d511f180/2dc579da` and `bc1d5164/ff28f65a` are now reproducible
  test cases — feed their `asp_code` from the JSONL into Clingo offline and
  watch the grounder's RSS.

That's H1, conclusively. The fix is then to throttle Clingo concurrency
(or run Clingo in a subprocess with `RLIMIT_AS`), but **that's a separate
change** — diagnosis first, then fix.

---

## Layer 2 — tracemalloc (added after run 22616269)

Run `22616269` confirmed the leak is in the python pipeline process and on
the chat-completion path: `self_rss` 1.6 GB → 182 GB over ~70 s during the
first completion wave, while `vllm_rss` and `engine_rss` stayed flat (1.4 GB
and 2.7 GB), only one `validate` warning fired, and `gc_objs` barely moved
(165 k → 171 k) — small number of *huge* objects, not many small ones.

External review pointed at httpx as a likely culprit. To pin the source line,
`tracemalloc` is started at the top of `main()` and a snapshot is taken
every `TRACEMALLOC_SNAPSHOT_EVERY=5` puzzle completions.

### What gets logged

After every 5th puzzle completion, four chunks of output:

```
TRACEMALLOC done=5 total=12.34GB tracked_blocks=45123 current=12.34GB peak=12.34GB
TRACEMALLOC done=5 by-package:
   8.123GB  blocks=     12345  http_stack(httpx)
   2.456GB  blocks=     34567  http_stack(httpcore)
   0.789GB  blocks=      8765  pydantic
   0.456GB  blocks=     12345  ours
   0.123GB  blocks=     56789  stdlib_or_other
TRACEMALLOC done=5 top-15 by file:line:
    1.  4.321GB  blocks=      4321  /…/httpx/_models.py:1234
    2.  2.111GB  blocks=      2111  /…/httpcore/_async/http11.py:567
    ...
TRACEMALLOC done=5 top allocator full traceback (4.321GB, blocks=4321):
   File "/…/httpx/_models.py", line 1234
   File "/…/openai/_response.py", line 89
   File "/…/llm/vllm_engine.py", line 84
   ...
```

### Field meanings

| Field | Source | What it tells you |
|---|---|---|
| `total` | sum over `Snapshot.statistics("lineno")` | currently-allocated bytes attributed to lines tracemalloc was watching |
| `tracked_blocks` | sum of `.count` | number of live allocations attributed |
| `current` | `tracemalloc.get_traced_memory()[0]` | current python-side allocation footprint, all sources |
| `peak` | `tracemalloc.get_traced_memory()[1]` | **monotonic** high-water mark; immune to between-snapshot bursts |
| `by-package` | `_classify_package` on each stat's top frame | grouped totals — the "is it httpx?" answer is here |
| `top-15 by file:line` | `Snapshot.statistics("lineno")[:15]` | the leaking line, if any single line dominates |
| top-allocator full traceback | `stat.traceback.format()` for `[0]` | full call chain into the leaking allocation; reveals e.g. openai → httpx → httpcore |

### Package classification

`_classify_package` in `src/utils/diagnostics.py` folds related libraries
into named buckets so the suspected stack is one row, not five:

| Bucket | Includes |
|---|---|
| `http_stack(<name>)` | `openai`, `httpx`, `httpcore`, `h11`, `h2`, `anyio` — the OpenAI client and everything beneath it down to the wire |
| `pydantic` | `pydantic`, `pydantic_core` — response model parsing and `model_dump()` |
| `stdlib_asyncio` | the stdlib `asyncio` package |
| `clingo` | the `clingo` Python extension's `.py` glue |
| `ours` | files under the project tree |
| `other_site_packages` / `stdlib_or_other` | everything else |

The HTTP stack appears as `http_stack(httpx)`, `http_stack(httpcore)`, etc.
— the parenthetical names the specific package the allocation came out of,
so you can tell whether bytes are sitting in httpx's `Response`, httpcore's
connection buffer, or h11's parser. Sum the rows starting with
`http_stack(` to get the HTTP-stack total in one number.

### How to interpret the next run

The interesting sample is **the snapshot taken just before OOM** — for run
22616269 that would have been at `done=60`. Compare it to the first
snapshot at `done=5`:

- **`http_stack(*)` rows dominate and grow proportionally with `done`** →
  HTTP-stack retention confirmed. Specifically:
  - `http_stack(httpx)` dominant → httpx is buffering response bodies
    (probably `Response.content` or a stream not being closed).
  - `http_stack(httpcore)` dominant → connection-level buffer (read buffer
    on the connection object).
  - `http_stack(openai)` dominant → the OpenAI client's response wrapper or
    its internal cache.
- **`pydantic` is the dominant bucket** → `resp.model_dump()` (or the
  `ChatCompletion` model itself) is retaining inflated copies of the
  response. The fix would target `vllm_engine._one_chat`.
- **`ours` is dominant** → the leak is in our code, not the client. The
  top-15 file:line block names the exact line.
- **`peak` is far above `total`** → there's also a transient burst that the
  current sample missed. Lower `TRACEMALLOC_SNAPSHOT_EVERY` to 1 or 2.

The "top allocator full traceback" line gives you the call chain for the
single biggest line. For an httpx leak that traceback typically looks like
`httpx/_models.py:<read>` ← `openai/_response.py:<parse>` ←
`vllm_engine.py:<_one_chat>`. That naming alone is usually enough to
identify which httpx/openai API is misused.

### Caveats

- **Tracemalloc has overhead.** Each tracked allocation carries a
  `TRACEMALLOC_FRAMES=5` deep traceback. CPU is the dominant cost; memory
  overhead is usually 10–25 % of the allocations being tracked. For a run
  this allocation-heavy, expect 10–20 % wall-clock slowdown. Acceptable for
  diagnosis.
- **C-allocations done outside CPython's `PyMem_*` API are invisible.**
  Specifically: any `malloc()` performed inside Clingo's grounder or inside
  CUDA/torch is NOT tracked. This is fine for the current investigation
  (the leak is python-side, run 22616269 confirmed) but worth knowing.
- **Snapshots are taken from the worker that completes the 5th puzzle.**
  Other workers continue running; their allocations *do* show up (snapshot
  is process-wide), but they're a moving target between samples.
- **`_last_snapshot_at` is module-level state.** If `batch_pipeline.main`
  is re-entered (it isn't currently), it would skip already-taken
  thresholds.

### Removal

Three lines in `batch_pipeline.py` (the import, the constant, and two
`maybe_take_snapshot(...)` calls plus `start_tracemalloc()`), and the new
functions in `src/utils/diagnostics.py` (`start_tracemalloc`,
`maybe_take_snapshot`, `_take_and_log_snapshot`, `_classify_package`,
plus the `_HTTP_STACK_PKGS` / `_PYDANTIC_PKGS` constants and the
`tracemalloc` / `defaultdict` imports). No call-signature changes.

---

## Follow-ups not in this patch

If the periodic sampler + per-call deltas are inconclusive, the next layers:

- **`/proc/<pid>/smaps_rollup`** — RSS broken down by mapping kind
  (Anonymous, File, Shared_Dirty). One-shot at OOM-imminence (or every Nth
  sample). Distinguishes "heap leak" from "IPC buffer growth" on the vLLM
  side.
- **`tracemalloc`** — Python-object attribution at file:line resolution.
  Right tool when H6 is in play. Has nontrivial overhead; gate behind a
  `TRACEMALLOC=1` env var.
- **`memray`** — C-and-Python allocation profiler. Heaviest, but the
  definitive answer when neither tracemalloc (Python only) nor RSS deltas
  (whole process) localize the leak.

---

## Configuration

| Knob | Where | Default | Effect |
|---|---|---|---|
| Sampler interval | `batch_pipeline.main:diagnostics_sampler(interval=10.0)` | 10 s | Cheaper at 30 s, but you may miss bursts shorter than the interval. 10 s is a compromise; the *peak* fields are immune to this anyway. |
| Per-call delta threshold | `batch_pipeline.RSS_DELTA_LOG_THRESHOLD_GB` | 1.0 GB | Lower → more lines, more noise, smaller leaks visible. 1 GB filters out normal Python heap churn while still catching anything that could plausibly OOM us. |

---

## Removing the patch

When the OOM is fixed, the diagnostic patch is two contained pieces:

- `src/utils/diagnostics.py` — delete the file.
- `src/batch_pipeline.py` — revert the import, the
  `RSS_DELTA_LOG_THRESHOLD_GB` constant, the sampler-task creation in
  `main()`, and the two `vmrss_gb()` wrappings in `handle_puzzle`.

No call signatures change, no concurrency model changes, no new dependencies.
