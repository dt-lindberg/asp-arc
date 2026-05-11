"""Process pool for Clingo grounding and solving with hard timeouts.

Why a process pool: clingo.Control.ground() has no cancellation API, and
Python threads cannot be force-killed. The previous threading-based timeout
returned to the caller on timeout but left the daemon thread running
indefinitely, with the C grounder still allocating. Under load (pathological
LLM-generated programs that time out across all 30 grid pairs of a puzzle)
this leaks tens of GB of RSS.

Each pool slot is a long-lived child process with its own (in_q, out_q)
pair. The parent enforces the timeout by giving up on out_q.get and
SIGKILLing+respawning the worker. Workers are also recycled after
CLINGO_WORKER_MAX_JOBS successful jobs to bound slow allocator drift.

Spawn (not fork) is used so respawns later in the run — when the parent
already has feeder threads from earlier mp.Queue use — can't deadlock.
"""

import multiprocessing as mp
import queue as queue_mod
import threading
from dataclasses import dataclass, field

from config.config import (
    CLINGO_MAX_MODELS,
    CLINGO_POOL_WORKERS,
    CLINGO_TIMEOUT,
    CLINGO_WORKER_MAX_JOBS,
)
from utils.logger import get_logger

logger = get_logger(__name__)


_POISON = "__POISON__"


@dataclass
class _JobResult:
    """Wire format: worker → parent. All fields must be picklable."""

    seq: int
    err: bool
    payload: object  # list[list[str]] on success; list[(int, str)] on parse error; [] otherwise
    log_records: list = field(default_factory=list)  # [(level_name, message), ...]


class _Context:
    """Placeholder context object passed to clingo.ground(). Kept for parity
    with the previous threading-based runner."""


def _do_ground_and_solve(program: str, log_records: list) -> tuple[bool, object]:
    """Body of one job. Returns (err, payload).

    Logs are accumulated into log_records for replay in the parent — workers
    don't configure their own logger.
    """
    from clingo.control import Control

    clingo_messages = []

    def _clingo_logger(code, message):
        clingo_messages.append((code, message))

    ctl = Control(
        [str(CLINGO_MAX_MODELS), "--warn=none", "--opt-mode=optN", "-t", "4"],
        logger=_clingo_logger,
    )
    models = []

    try:
        program_clean = program.encode("ascii", errors="replace").decode("ascii")
        ctl.add("base", [], program_clean)
    except RuntimeError as e:
        log_records.append(
            ("debug", f"Clingo parse error: {e} ({len(clingo_messages)} messages)")
        )
        return True, clingo_messages
    except Exception as e:
        log_records.append(("error", f"Clingo add() failed: {e}"))
        return True, []

    try:
        ctl.ground([("base", [])], context=_Context())
    except RuntimeError as e:
        log_records.append(("debug", f"Clingo grounding error: {e}"))
        return True, clingo_messages
    except Exception as e:
        log_records.append(("error", f"Clingo grounding failed: {e}"))
        return True, []

    on_model = lambda model: models.append(model.symbols(atoms=True))  # noqa: E731

    with ctl.solve(on_model=on_model, async_=True) as handle:
        finished = handle.wait(CLINGO_TIMEOUT)
        if not finished:
            handle.cancel()
            handle.wait()
            log_records.append(
                ("debug", f"Clingo solve() timed out ({len(models)} models so far)")
            )
            return True, []

    models = [[str(atom) for atom in m] for m in models]
    log_records.append(("debug", f"Clingo: {len(models)} answer set(s)"))
    return False, models


def _worker_main(in_q: mp.Queue, out_q: mp.Queue) -> None:
    """Long-lived worker loop.

    Pull (seq, program) from in_q, put _JobResult on out_q. Exit on _POISON.
    """
    while True:
        try:
            msg = in_q.get()
        except (EOFError, OSError, KeyboardInterrupt):
            return
        if msg == _POISON:
            return
        seq, program = msg
        log_records: list = []
        try:
            err, payload = _do_ground_and_solve(program, log_records)
        except Exception as e:
            log_records.append(
                ("error", f"Clingo worker exception: {type(e).__name__}: {e}")
            )
            err, payload = True, []
        try:
            out_q.put(
                _JobResult(seq=seq, err=err, payload=payload, log_records=log_records)
            )
        except Exception:
            return


class _Worker:
    """One pool slot: a child process with its own queue pair."""

    def __init__(self, slot_id: int, ctx: mp.context.BaseContext):
        self.slot_id = slot_id
        self.ctx = ctx
        self.proc: mp.Process | None = None
        self.in_q: mp.Queue | None = None
        self.out_q: mp.Queue | None = None
        self.jobs_done: int = 0
        self._next_seq: int = 0
        self._spawn()

    def _spawn(self) -> None:
        self.in_q = self.ctx.Queue()
        self.out_q = self.ctx.Queue()
        self.proc = self.ctx.Process(
            target=_worker_main,
            args=(self.in_q, self.out_q),
            daemon=True,
            name=f"clingo-worker-{self.slot_id}",
        )
        self.proc.start()
        logger.debug(f"clingo-worker-{self.slot_id}: started pid={self.proc.pid}")

    def _close_queues(self) -> None:
        for q in (self.in_q, self.out_q):
            if q is None:
                continue
            try:
                q.close()
                q.join_thread()
            except Exception:
                pass
        self.in_q = None
        self.out_q = None

    def _kill(self) -> None:
        if self.proc is not None and self.proc.is_alive():
            try:
                self.proc.kill()
                self.proc.join(timeout=2.0)
            except Exception:
                pass
        try:
            if self.proc is not None:
                self.proc.close()
        except Exception:
            pass
        self.proc = None
        self._close_queues()

    def _kill_and_respawn(self) -> None:
        self._kill()
        self.jobs_done = 0
        self._next_seq = 0
        self._spawn()

    def run(
        self, program: str, timeout: float, max_jobs: int
    ) -> tuple[bool, object]:
        """Submit a program, wait up to `timeout`. Replays worker logs."""
        self._next_seq += 1
        seq = self._next_seq
        try:
            self.in_q.put((seq, program))
        except Exception as e:
            logger.warning(
                f"clingo-worker-{self.slot_id}: send failed ({e}); respawning"
            )
            self._kill_and_respawn()
            return True, []

        try:
            result: _JobResult = self.out_q.get(timeout=timeout)
        except queue_mod.Empty:
            logger.debug(
                f"Clingo timed out after {timeout}s; killing "
                f"clingo-worker-{self.slot_id}"
            )
            self._kill_and_respawn()
            return True, []
        except (EOFError, OSError) as e:
            logger.warning(
                f"clingo-worker-{self.slot_id}: queue read failed ({e}); respawning"
            )
            self._kill_and_respawn()
            return True, []

        for level, msg in result.log_records:
            getattr(logger, level, logger.debug)(msg)

        if result.seq != seq:
            logger.warning(
                f"clingo-worker-{self.slot_id}: seq mismatch "
                f"(got={result.seq} want={seq}); respawning"
            )
            self._kill_and_respawn()
            return True, []

        self.jobs_done += 1
        if self.jobs_done >= max_jobs:
            logger.debug(
                f"clingo-worker-{self.slot_id}: recycling after {self.jobs_done} jobs"
            )
            self._kill_and_respawn()

        return result.err, result.payload


class ClingoPool:
    """Fixed-size pool of grounding/solving worker processes."""

    def __init__(
        self,
        n_workers: int = CLINGO_POOL_WORKERS,
        max_jobs_per_worker: int = CLINGO_WORKER_MAX_JOBS,
        start_method: str = "spawn",
    ):
        self.n_workers = n_workers
        self.max_jobs_per_worker = max_jobs_per_worker
        self._ctx = mp.get_context(start_method)
        self._workers = [_Worker(i, self._ctx) for i in range(n_workers)]
        self._free: queue_mod.Queue = queue_mod.Queue()
        for w in self._workers:
            self._free.put(w)
        self._closed_lock = threading.Lock()
        self._closed = False
        logger.info(
            f"ClingoPool: {n_workers} workers spawned "
            f"(start_method={start_method}, max_jobs_per_worker={max_jobs_per_worker})"
        )

    def run(
        self, program: str, timeout: float = CLINGO_TIMEOUT
    ) -> tuple[bool, object]:
        """Run one program through the pool. Blocking; safe from any thread."""
        if self._closed:
            raise RuntimeError("ClingoPool is closed")
        worker = self._free.get()
        try:
            return worker.run(program, timeout, self.max_jobs_per_worker)
        finally:
            self._free.put(worker)

    def shutdown(self) -> None:
        with self._closed_lock:
            if self._closed:
                return
            self._closed = True
        logger.info("ClingoPool: shutting down")
        for w in self._workers:
            try:
                if w.in_q is not None:
                    w.in_q.put(_POISON)
            except Exception:
                pass
        for w in self._workers:
            if w.proc is not None and w.proc.is_alive():
                w.proc.join(timeout=2.0)
                if w.proc.is_alive():
                    w.proc.kill()
                    w.proc.join(timeout=2.0)
        logger.info("ClingoPool: shutdown complete")


_pool: ClingoPool | None = None
_pool_lock = threading.Lock()


def init_pool(
    n_workers: int = CLINGO_POOL_WORKERS,
    max_jobs_per_worker: int = CLINGO_WORKER_MAX_JOBS,
) -> ClingoPool:
    """Create the singleton pool. Idempotent — second call returns the same pool."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        _pool = ClingoPool(
            n_workers=n_workers,
            max_jobs_per_worker=max_jobs_per_worker,
        )
        return _pool


def get_pool() -> ClingoPool:
    """Return the singleton pool, lazily initializing it on first call."""
    if _pool is None:
        return init_pool()
    return _pool


def shutdown_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is None:
            return
        _pool.shutdown()
        _pool = None
