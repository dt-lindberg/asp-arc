"""
compare_runs.py — tabulate solve rate and error categories for asp-arc audit runs.

Usage:
    python compare_runs.py [<audit_dir_pattern> ...]

If no patterns are given, scans `src/audit/*` and groups by the trailing `_<tag>`
suffix in the run_id (set by main.py --tag), assuming tags follow the
`<variant>_s<seed>` convention used by run_variant.job.

Outputs a per-run summary plus a per-variant aggregate (mean ± stdev across seeds).
"""

import argparse
import glob
import json
import os
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

AUDIT_ROOT = Path("/home/dlindberg/Projects/asp-arc/src/audit")


def categorize_clingo_error(msg: str) -> str:
    if not msg:
        return "empty"
    m = msg.lower()
    if "unsafe variable" in m:
        return "unsafe_var"
    if "syntax error" in m or "parsing failed" in m:
        return "syntax"
    if "atom does not occur in any rule head" in m:
        return "undefined_atom"
    if "operator" in m and "expected" in m:
        return "operator"
    if "unbound" in m:
        return "unbound"
    if "redefin" in m:
        return "redefined"
    if "timeout" in m or "timed out" in m:
        return "timeout"
    return "other"


def load_run(run_dir: Path) -> dict:
    """Aggregate one run_id's audit JSONs."""
    files = sorted(run_dir.glob("*.json"))
    n_puzzles = 0
    n_solved = 0
    n_solved_initial = 0
    # status counters across (puzzle, attempt, example) triples
    status_counts = Counter()
    error_categories = Counter()
    # per-attempt index tracker
    attempt_progress = defaultdict(lambda: Counter())  # attempt_idx -> status counts
    program_lengths = []
    n_empty_program = 0
    n_attempts_total = 0

    for fp in files:
        with open(fp) as f:
            d = json.load(f)
        n_puzzles += 1
        if d.get("final_correct"):
            n_solved += 1
        # initial
        init = d["steps"].get("initial") or {}
        if init.get("all_train_correct"):
            n_solved_initial += 1

        # iterate over initial + refinements
        attempts = []
        if init:
            attempts.append(("initial", init))
        for ref in d.get("refinements", []) or []:
            attempts.append((f"refinement_{ref.get('attempt', '?')}", ref))

        for tag, att in attempts:
            n_attempts_total += 1
            prog = att.get("program") or ""
            program_lengths.append(len(prog))
            if not prog.strip():
                n_empty_program += 1
            for tv in att.get("train_verifications") or []:
                status = tv.get("status", "unknown")
                status_counts[status] += 1
                attempt_progress[tag][status] += 1
                if status == "clingo_error":
                    error_categories[categorize_clingo_error(tv.get("clingo_errors") or "")] += 1

    return {
        "run_dir": run_dir.name,
        "n_puzzles": n_puzzles,
        "n_solved": n_solved,
        "n_solved_initial": n_solved_initial,
        "n_attempts_total": n_attempts_total,
        "n_empty_program": n_empty_program,
        "mean_program_len": statistics.mean(program_lengths) if program_lengths else 0,
        "status_counts": dict(status_counts),
        "error_categories": dict(error_categories),
        "attempt_progress": {k: dict(v) for k, v in attempt_progress.items()},
    }


def variant_seed_from_tag(name: str):
    """Extract (variant, seed) from a run_id name like '20260426_175200_v0_s132'."""
    m = re.match(r"^\d{8}_\d{6}_(v\d+)_s(\d+)$", name)
    if m:
        return m.group(1), int(m.group(2))
    return None, None


def fmt_pct(num, den):
    return f"{num}/{den} ({100 * num / den:.0f}%)" if den else "0/0"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*", help="Audit dirs (default: scan src/audit/*_v*_s*)")
    ap.add_argument("--all", action="store_true", help="Show all audit dirs, not just tagged ones")
    args = ap.parse_args()

    if args.dirs:
        run_dirs = [Path(d) for d in args.dirs]
    else:
        pattern = "*_v*_s*" if not args.all else "*"
        run_dirs = sorted(AUDIT_ROOT.glob(pattern))

    if not run_dirs:
        print("No matching audit dirs found.")
        return

    runs = [load_run(d) for d in run_dirs if d.is_dir()]

    # Print per-run table
    print(f"\n=== Per-run summary ({len(runs)} runs) ===\n")
    hdr = f"{'run_id':<36}  {'variant':<4}  {'seed':<5}  {'solved':>10}  {'init':>7}  {'cling_err':>9}  {'unsafe':>6}  {'syntax':>6}  {'unsat':>6}  {'wrong':>6}  {'empty':>5}"
    print(hdr)
    print("-" * len(hdr))
    rows = []
    for r in runs:
        v, s = variant_seed_from_tag(r["run_dir"])
        sc = r["status_counts"]
        ec = r["error_categories"]
        total_verifs = sum(sc.values())
        clingo = sc.get("clingo_error", 0)
        row = {
            "run_dir": r["run_dir"],
            "variant": v or "?",
            "seed": s if s is not None else "?",
            "n_puzzles": r["n_puzzles"],
            "n_solved": r["n_solved"],
            "n_solved_initial": r["n_solved_initial"],
            "n_total_verifs": total_verifs,
            "clingo_error": clingo,
            "unsafe_var": ec.get("unsafe_var", 0),
            "syntax": ec.get("syntax", 0),
            "unsatisfiable": sc.get("unsatisfiable", 0),
            "wrong_values": sc.get("wrong_values", 0),
            "n_empty_program": r["n_empty_program"],
            "mean_program_len": r["mean_program_len"],
        }
        rows.append(row)
        print(
            f"{r['run_dir']:<36}  {row['variant']:<4}  {str(row['seed']):<5}  "
            f"{fmt_pct(row['n_solved'], row['n_puzzles']):>10}  "
            f"{row['n_solved_initial']:>7}  "
            f"{row['clingo_error']:>9}  {row['unsafe_var']:>6}  {row['syntax']:>6}  "
            f"{row['unsatisfiable']:>6}  {row['wrong_values']:>6}  {row['n_empty_program']:>5}"
        )

    # Aggregate per variant
    by_variant = defaultdict(list)
    for row in rows:
        if row["variant"] != "?":
            by_variant[row["variant"]].append(row)

    if by_variant:
        print(f"\n=== Per-variant aggregate ({len(by_variant)} variants) ===\n")
        keys = ["n_solved", "n_solved_initial", "clingo_error", "unsafe_var", "syntax",
                "unsatisfiable", "wrong_values", "n_empty_program", "mean_program_len"]
        hdr = f"{'variant':<8}  {'n_seeds':<7}  " + "  ".join(f"{k[:14]:>14}" for k in keys)
        print(hdr)
        print("-" * len(hdr))
        for variant in sorted(by_variant.keys()):
            seeds = by_variant[variant]
            line = f"{variant:<8}  {len(seeds):<7}  "
            for k in keys:
                vals = [s[k] for s in seeds]
                m = statistics.mean(vals)
                sd = statistics.stdev(vals) if len(vals) > 1 else 0
                line += f"  {m:>6.1f}±{sd:>5.1f}"
            print(line)


if __name__ == "__main__":
    main()
