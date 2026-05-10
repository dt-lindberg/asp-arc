"""Memory-and-concurrency diagnostics for OOM root-causing.

Pure observation — no behavior changes, no throttling. The sampler logs a
single line every `interval` seconds with everything needed to localize a
burst (which process grew, by how much, are tasks/threads leaking).

Per-call RSS deltas around chat_async and validate_asp_program are emitted
inline by batch_pipeline.py via `vmrss_gb()`.

See docs/diagnostics.md for interpretation.
"""

import asyncio
import gc
import os
import threading
import tracemalloc
from collections import defaultdict
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

# How many stack frames tracemalloc keeps per allocation. More frames = better
# attribution but higher overhead. 5 is enough to walk from a hot allocation
# back through openai → httpx → httpcore.
TRACEMALLOC_FRAMES = 5

# Packages to fold into the "http_stack" group when summarizing tracemalloc.
# This is the suspected culprit per the external review; grouping them makes
# the HTTP stack's footprint a single number in the snapshot summary.
_HTTP_STACK_PKGS = ("openai", "httpx", "httpcore", "h11", "h2", "anyio")
_PYDANTIC_PKGS = ("pydantic", "pydantic_core")


def vmrss_gb(pid: int | None = None) -> float | None:
    """Current resident memory of `pid` (default: self) in GB. None on read failure."""
    target = pid if pid is not None else "self"
    p = Path(f"/proc/{target}/status")
    try:
        for line in p.read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1e6  # KB → GB
    except (OSError, ValueError) as e:
        logger.warning(f"DIAG vmrss_gb({target}) failed: {type(e).__name__}: {e}")
        return None
    logger.warning(f"DIAG vmrss_gb({target}): no VmRSS line in {p}")
    return None


def vmhwm_gb(pid: int | None = None) -> float | None:
    """High-water mark of resident memory for `pid` since it started, in GB."""
    target = pid if pid is not None else "self"
    p = Path(f"/proc/{target}/status")
    try:
        for line in p.read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1e6
    except (OSError, ValueError) as e:
        logger.warning(f"DIAG vmhwm_gb({target}) failed: {type(e).__name__}: {e}")
        return None
    logger.warning(f"DIAG vmhwm_gb({target}): no VmHWM line in {p}")
    return None


def _resolve_cgroup_memory_peak_path() -> Path | None:
    """Locate the cgroup v2 memory.peak file for this process.

    /proc/self/cgroup looks like '0::/some/slurm/leaf'; the corresponding
    sysfs path is /sys/fs/cgroup/<that suffix>/memory.peak.
    """
    try:
        line = Path("/proc/self/cgroup").read_text().strip()
    except OSError as e:
        logger.warning(f"DIAG cannot read /proc/self/cgroup: {type(e).__name__}: {e}")
        return None
    # cgroup v2 has a single line: "0::/path"
    parts = line.split("::")
    if len(parts) != 2:
        logger.warning(
            f"DIAG /proc/self/cgroup not v2 format (expected '0::/path', got {line!r}); "
            f"cgroup_peak will be unavailable"
        )
        return None
    suffix = parts[1].lstrip("/")
    candidate = Path("/sys/fs/cgroup") / suffix / "memory.peak"
    if not candidate.exists():
        logger.warning(
            f"DIAG cgroup memory.peak not found at {candidate}; "
            f"cgroup_peak will be unavailable"
        )
        return None
    return candidate


def cgroup_peak_gb(path: Path | None = None) -> float | None:
    """Cgroup memory.peak (kernel watermark, monotonic) in GB. None if unavailable."""
    if path is None:
        path = _resolve_cgroup_memory_peak_path()
    if path is None:
        return None
    try:
        return int(path.read_text().strip()) / 1e9
    except (OSError, ValueError) as e:
        logger.warning(f"DIAG cgroup_peak_gb({path}) failed: {type(e).__name__}: {e}")
        return None


def find_pids_by_cmdline(needle: str) -> list[int]:
    """Return PIDs whose /proc/<pid>/cmdline contains `needle`."""
    pids = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            )
        except OSError:
            # /proc/<pid> entries disappear when processes exit between iterdir
            # and read; this is normal kernel race, not a diagnostic failure.
            continue
        if needle in cmdline:
            pids.append(int(entry.name))
    return pids


def discover_vllm_pids() -> dict[str, int | None]:
    """Best-effort lookup of (api_server_pid, engine_core_pid).

    vLLM spawns multiple processes; we identify the API server by 'vllm'
    in cmdline, and the engine core by 'EngineCore' or 'VLLM::EngineCore'.
    Returns Nones for components we can't find.
    """
    api_pids = find_pids_by_cmdline("vllm")
    engine_pids = find_pids_by_cmdline("EngineCore")
    self_pid = os.getpid()
    api_pids = [p for p in api_pids if p != self_pid]
    engine_pids = [p for p in engine_pids if p != self_pid]
    return {
        "vllm_any": api_pids[0] if api_pids else None,
        "engine": engine_pids[0] if engine_pids else None,
    }


def start_tracemalloc(frames: int = TRACEMALLOC_FRAMES) -> bool:
    """Start tracemalloc with the given frame depth. Idempotent; safe to call twice."""
    if tracemalloc.is_tracing():
        logger.info("DIAG tracemalloc already tracing; not restarting")
        return True
    try:
        tracemalloc.start(frames)
    except Exception as e:
        logger.warning(f"DIAG tracemalloc.start({frames}) failed: {type(e).__name__}: {e}")
        return False
    logger.info(f"DIAG tracemalloc started (frames={frames})")
    return True


def _classify_package(filepath: str) -> str:
    """Map an allocation's source file to a coarse package label.

    The HTTP stack (openai client + httpx + httpcore + h11/h2 + anyio) is
    folded into one bucket because the suspected culprit lives somewhere in
    that chain and a single number is more useful than five fragmented ones.
    """
    if not filepath:
        return "other"
    norm = filepath.replace(os.sep, "/")
    for pkg in _HTTP_STACK_PKGS:
        if f"/{pkg}/" in norm or norm.endswith(f"/{pkg}.py"):
            return f"http_stack({pkg})"
    for pkg in _PYDANTIC_PKGS:
        if f"/{pkg}/" in norm or norm.endswith(f"/{pkg}.py"):
            return "pydantic"
    if "/asyncio/" in norm:
        return "stdlib_asyncio"
    if "/clingo/" in norm:
        return "clingo"
    # Project files live under the working directory hierarchy.
    if "/asp-arc-gpt-oss-nvarc/" in norm or "/src/" in norm:
        return "ours"
    if "/site-packages/" in norm:
        return "other_site_packages"
    return "stdlib_or_other"


_snapshot_lock = threading.Lock()
_last_snapshot_at = -1


def maybe_take_snapshot(done_count: int, every: int = 5) -> None:
    """Take and log a tracemalloc snapshot when `done_count` crosses a multiple of `every`.

    Safe to call from multiple workers/threads — the decision is locked, and
    repeated calls at the same `done_count` snapshot at most once.
    """
    global _last_snapshot_at
    if not tracemalloc.is_tracing():
        return
    with _snapshot_lock:
        target = (done_count // every) * every
        if target <= _last_snapshot_at or target == 0:
            return
        _last_snapshot_at = target
    try:
        _take_and_log_snapshot(done_count)
    except Exception as e:
        logger.warning(
            f"DIAG tracemalloc snapshot failed at done={done_count}: "
            f"{type(e).__name__}: {e}"
        )


def _take_and_log_snapshot(done_count: int) -> None:
    """Capture a snapshot and emit a multi-line TRACEMALLOC report at INFO.

    Output:
      TRACEMALLOC done=N total=X.XXGB tracked_blocks=Y current=Z.ZZGB peak=W.WWGB
      TRACEMALLOC done=N by-package:
        S.SSGB  blocks=B  pkg
        ...
      TRACEMALLOC done=N top-15 by file:line:
        S.SSGB  blocks=B  filename:line   <-- traceback frames after this if present
        ...

    `total` is the sum of currently-allocated bytes attributed to Python
    allocations that tracemalloc was watching at allocation time. `current`
    and `peak` come from tracemalloc.get_traced_memory() — `peak` is monotonic
    over the whole run and is the best single watermark.
    """
    snap = tracemalloc.take_snapshot()
    current, peak = tracemalloc.get_traced_memory()

    stats_by_line = snap.statistics("lineno")
    total = sum(s.size for s in stats_by_line)
    blocks = sum(s.count for s in stats_by_line)

    logger.info(
        f"TRACEMALLOC done={done_count} total={total / 1e9:.2f}GB tracked_blocks={blocks} "
        f"current={current / 1e9:.2f}GB peak={peak / 1e9:.2f}GB"
    )

    # Per-package totals, sorted descending. The HTTP stack is the suspected
    # leak; if it dominates here, that's the answer.
    pkg_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"size": 0, "count": 0}
    )
    for stat in stats_by_line:
        if not stat.traceback:
            pkg = "no_traceback"
        else:
            pkg = _classify_package(stat.traceback[0].filename)
        pkg_totals[pkg]["size"] += stat.size
        pkg_totals[pkg]["count"] += stat.count

    logger.info(f"TRACEMALLOC done={done_count} by-package:")
    for pkg, info in sorted(pkg_totals.items(), key=lambda x: -x[1]["size"]):
        logger.info(
            f"  {info['size'] / 1e9:6.3f}GB  blocks={info['count']:>10d}  {pkg}"
        )

    # Top-N file:line allocators with one frame of context. Limit to 15 to
    # keep the slurm log readable while still surfacing minor offenders.
    logger.info(f"TRACEMALLOC done={done_count} top-15 by file:line:")
    for i, stat in enumerate(stats_by_line[:15], 1):
        frame = stat.traceback[0] if stat.traceback else None
        loc = f"{frame.filename}:{frame.lineno}" if frame else "<no traceback>"
        logger.info(
            f"  {i:>2d}. {stat.size / 1e9:6.3f}GB  blocks={stat.count:>10d}  {loc}"
        )

    # For the single biggest allocator, dump its full traceback. This is
    # where you'll see e.g. openai/_response.py → httpx/_models.py → httpcore.
    if stats_by_line:
        top = stats_by_line[0]
        logger.info(
            f"TRACEMALLOC done={done_count} top allocator full traceback "
            f"({top.size / 1e9:.3f}GB, blocks={top.count}):"
        )
        for line in top.traceback.format():
            logger.info(f"  {line}")


async def diagnostics_sampler(interval: float = 10.0) -> None:
    """Run forever, log a one-line memory+concurrency snapshot every `interval` s.

    Logged line shape (single line, space-separated key=value):
      DIAG cgroup_peak=78.4GB self_rss=12.1GB self_hwm=14.0GB
           vllm_rss=66.0GB vllm_hwm=66.2GB engine_rss=2.1GB
           tasks=103 threads=22 gc_objs=842113

    Field meanings:
      cgroup_peak — kernel watermark of the whole cgroup (this+vllm+children).
                    Monotonic: it only goes up, so a jump between samples is a burst.
      self_rss/hwm — current and high-water RSS of the python pipeline.
      vllm_rss/hwm — same for the vLLM API server process.
      engine_rss   — vLLM's EngineCore process (the one with the GPU).
      tasks        — len(asyncio.all_tasks()); growth means coroutines aren't completing.
      threads      — len(threading.enumerate()); growth means Clingo threads leak.
      gc_objs      — len(gc.get_objects()); growth means python objects accumulate.

    Path resolution and PID discovery happen once on first iteration; any
    field that fails to read is omitted from the line.
    """
    cgroup_path = _resolve_cgroup_memory_peak_path()
    pids = discover_vllm_pids()

    logger.info(
        f"DIAG init  cgroup_peak_path={cgroup_path}  "
        f"vllm_pid={pids['vllm_any']}  engine_pid={pids['engine']}  "
        f"self_pid={os.getpid()}"
    )

    while True:
        fields = []

        peak = cgroup_peak_gb(cgroup_path)
        if peak is not None:
            fields.append(f"cgroup_peak={peak:.1f}GB")

        srss, shwm = vmrss_gb(), vmhwm_gb()
        if srss is not None:
            fields.append(f"self_rss={srss:.1f}GB")
        if shwm is not None:
            fields.append(f"self_hwm={shwm:.1f}GB")

        if pids["vllm_any"] is not None:
            vrss = vmrss_gb(pids["vllm_any"])
            vhwm = vmhwm_gb(pids["vllm_any"])
            if vrss is not None:
                fields.append(f"vllm_rss={vrss:.1f}GB")
            if vhwm is not None:
                fields.append(f"vllm_hwm={vhwm:.1f}GB")

        if pids["engine"] is not None:
            erss = vmrss_gb(pids["engine"])
            if erss is not None:
                fields.append(f"engine_rss={erss:.1f}GB")

        try:
            fields.append(f"tasks={len(asyncio.all_tasks())}")
        except RuntimeError as e:
            logger.warning(
                f"DIAG asyncio.all_tasks() raised {type(e).__name__}: {e} "
                f"(no running loop?); 'tasks' field omitted this sample"
            )
        fields.append(f"threads={threading.active_count()}")
        fields.append(f"gc_objs={len(gc.get_objects())}")

        logger.info("DIAG " + " ".join(fields))

        await asyncio.sleep(interval)
