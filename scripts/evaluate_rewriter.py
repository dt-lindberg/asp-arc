"""Evaluate the deterministic rewriter against the historic audit corpus.

Walks every JSON under ``~/Projects/asp-arc/src/audit/*/*.json``, collects
every (program, status='clingo_error') pair, deduplicates programs, runs
each program through ``ASPRewriter`` and Clingo before/after, and tallies:

  - ``original_passes``  -- baseline (programs that already grounded)
  - ``rewritten_passes`` -- programs that ground after rewriting
  - ``fixed``            -- previously broken, now grounds
  - ``regressions``      -- previously grounded, now broken (should be 0)
  - per-rule fire count  -- how many programs each rule touched
  - residual error breakdown (top error messages after rewriting)

A row is appended to ``results/rewriter_progress.csv`` with the cumulative
metric for the current rule set.  Which rules are active is controlled by
the ``RULES`` constant below — the loop driver edits this file (or runs
with ``--rules a,b,c``) to add a rule and re-evaluates.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.rewriter import ALL_RULES, ASPRewriter  # noqa: E402

import clingo  # noqa: E402

AUDIT_GLOB = os.path.expanduser("~/Projects/asp-arc/src/audit/*/*.json")
RESULTS_DIR = ROOT / "results"
PROGRESS_CSV = RESULTS_DIR / "rewriter_progress.csv"

# Stub injected so partially-grounded programs can still exercise their rule
# heads.  Mirrors what the live pipeline does at verification time.
STUB = """
% --- evaluator stub ---
input(0,0,0). input(0,1,0). input(1,0,0). input(1,1,0).
color(0). color(1). color(2). color(3). color(4).
color(5). color(6). color(7). color(8). color(9).
% --- end stub ---
"""


@dataclass
class Outcome:
    ok: bool
    error: str  # empty when ok


import multiprocessing as _mp


def _ground_worker(program: str, q):
    import os as _os

    devnull = _os.open(_os.devnull, _os.O_WRONLY)
    _os.dup2(devnull, 1)
    _os.dup2(devnull, 2)
    try:
        ctl = clingo.Control(["--warn=none", "-t", "1"])
        ctl.add("base", [], STUB + "\n" + program)
        ctl.ground([("base", [])])
        q.put(("ok", ""))
    except Exception as e:
        q.put(("err", str(e).splitlines()[0][:200] if str(e) else type(e).__name__))


def grounds(program: str, timeout: float = 6.0) -> Outcome:
    """Run grounding in a subprocess so we can hard-kill on timeout."""
    q: _mp.Queue = _mp.Queue()
    p = _mp.Process(target=_ground_worker, args=(program, q))
    p.start()
    p.join(timeout)
    if p.is_alive():
        p.terminate()
        p.join(1.0)
        if p.is_alive():
            p.kill()
            p.join(0.5)
        return Outcome(False, "ground_timeout")
    if q.empty():
        return Outcome(False, "ground_crashed")
    tag, msg = q.get()
    return Outcome(tag == "ok", msg)


def collect_broken_programs() -> list[str]:
    """Return deduplicated list of programs that produced ``clingo_error``
    in at least one training-example verification.
    """
    seen: set[str] = set()
    progs: list[str] = []
    files = sorted(glob.glob(AUDIT_GLOB))
    for f in files:
        try:
            data = json.load(open(f))
        except Exception:
            continue
        items = []
        if "steps" in data and isinstance(data["steps"], dict):
            init = data["steps"].get("initial")
            if init:
                items.append(init)
        items.extend(data.get("refinements", []) or [])
        for it in items:
            prog = (it.get("program") or "").strip()
            if not prog or prog in seen:
                continue
            verifs = it.get("train_verifications") or []
            if any(v.get("status") == "clingo_error" for v in verifs):
                seen.add(prog)
                progs.append(prog)
    return progs


def normalize_error(err: str) -> str:
    """Drop file:line locations so we can group errors."""
    return re.sub(r"<block>:\d+(:\d+(-\d+)?)?:?", "", err).strip()


_GROUND_CACHE: dict[str, Outcome] = {}


def grounds_cached(program: str) -> Outcome:
    if program in _GROUND_CACHE:
        return _GROUND_CACHE[program]
    o = grounds(program)
    _GROUND_CACHE[program] = o
    return o


def evaluate(rule_names: list[str], short_desc: str, write_csv: bool = True, progs=None):
    rules = [r for r in ALL_RULES if r.name in rule_names]
    rewriter = ASPRewriter(rules)
    if progs is None:
        progs = collect_broken_programs()

    fire_counts: Counter[str] = Counter()
    fixed = 0
    still_broken = 0
    regressions = 0
    original_grounds_n = 0
    residual_errors: Counter[str] = Counter()

    for prog in progs:
        before = grounds_cached(prog)
        rewritten, fired = rewriter.rewrite_with_trace(prog)
        for r in fired:
            fire_counts[r] += 1
        after = grounds_cached(rewritten) if rewritten != prog else before

        if before.ok:
            original_grounds_n += 1
        if before.ok and not after.ok:
            regressions += 1
        if not before.ok and after.ok:
            fixed += 1
        if not before.ok and not after.ok:
            still_broken += 1
            residual_errors[normalize_error(after.error)] += 1

    n = len(progs)

    print(f"\n{'=' * 72}")
    print(f"Rules active ({len(rule_names)}): {rule_names}")
    print(f"Description: {short_desc}")
    print(f"Total broken programs: {n}")
    print(f"Originally ground OK (sanity): {original_grounds_n}")
    print(f"Fixed by rewriter: {fixed}  ({fixed / n * 100:.1f}%)")
    print(f"Regressions (formerly OK, now broken): {regressions}")
    print(f"Still broken: {still_broken}")
    print(f"\nPer-rule fire counts:")
    for r in rule_names:
        print(f"  {r:30s}  {fire_counts.get(r, 0):4d}")
    print(f"\nTop residual errors (after rewriting):")
    for err, c in residual_errors.most_common(8):
        print(f"  {c:4d}  {err[:120]}")

    if write_csv:
        RESULTS_DIR.mkdir(exist_ok=True)
        new_file = not PROGRESS_CSV.exists()
        # We need delta_fixed vs the previous row.
        prev_total = 0
        if not new_file:
            with PROGRESS_CSV.open() as fh:
                rows = list(csv.DictReader(fh))
                if rows:
                    prev_total = int(rows[-1]["total_fixed"])
        delta = fixed - prev_total
        with PROGRESS_CSV.open("a", newline="") as fh:
            writer = csv.writer(fh)
            if new_file:
                writer.writerow(
                    [
                        "rule_index",
                        "rule_name",
                        "short_description",
                        "total_fixed",
                        "delta_fixed",
                        "regressions",
                        "n_total",
                    ]
                )
            writer.writerow(
                [
                    len(rule_names),
                    rule_names[-1] if rule_names else "baseline",
                    short_desc,
                    fixed,
                    delta,
                    regressions,
                    n,
                ]
            )
        print(f"\nAppended row to {PROGRESS_CSV}")

    return {
        "fixed": fixed,
        "regressions": regressions,
        "still_broken": still_broken,
        "n": n,
    }


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument(
        "--rules",
        default="",
        help="Comma-separated rule names; empty means baseline (no rules).",
    )
    p.add_argument(
        "--desc",
        default="",
        help="Short two-word description for the plot label.",
    )
    p.add_argument(
        "--no-csv",
        action="store_true",
        help="Skip appending to results CSV (for ad-hoc runs).",
    )
    p.add_argument(
        "--sweep",
        default="",
        help=(
            "Comma-separated rule names — run baseline then add each rule one"
            " at a time, appending a CSV row for every step. Resets the CSV."
        ),
    )
    args = p.parse_args(argv)

    if args.sweep:
        sweep = [r for r in args.sweep.split(",") if r]
        if PROGRESS_CSV.exists():
            PROGRESS_CSV.unlink()
        progs = collect_broken_programs()
        print(f"Loaded {len(progs)} unique broken programs")
        # baseline
        evaluate([], "baseline", write_csv=True, progs=progs)
        active: list[str] = []
        descs = {r.name: r.short for r in ALL_RULES}
        for name in sweep:
            active.append(name)
            evaluate(active.copy(), descs.get(name, name), write_csv=True, progs=progs)
        print(f"\nGround cache size: {len(_GROUND_CACHE)} unique programs")
        return

    rule_names = [r for r in args.rules.split(",") if r]
    desc = args.desc or (rule_names[-1] if rule_names else "baseline")
    evaluate(rule_names, desc, write_csv=not args.no_csv)


if __name__ == "__main__":
    main()
