"""Aggregate summary of asp-arc multi-seed experiment results.

Usage:
    python scripts/summarize_arc_runs.py results/seed11_20260427_120000

Scans all per-puzzle JSONs under one run directory and prints a
summary table compatible with the asp-arc-rewrites analysis style.

Also supports wildcard:
    python scripts/summarize_arc_runs.py results/seed*_2026*
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys


def _load_run_meta(run_dir: str) -> dict | None:
    meta_path = os.path.join(run_dir, "_run_meta.json")
    if os.path.isfile(meta_path):
        with open(meta_path) as f:
            return json.load(f)
    return None


def _is_puzzle_file(path: str) -> bool:
    base = os.path.basename(path)
    return base.endswith(".json") and not base.startswith("_")


def _count_parse_failures(syntax_agent: dict | None) -> int:
    """Count rounds where tool_call parsing failed in the syntax agent steps."""
    if not syntax_agent or not isinstance(syntax_agent, dict):
        return 0
    steps = syntax_agent.get("steps", [])
    if not steps:
        return 0
    n = 0
    for step in steps:
        # tool_call is None AND no direct code was extracted → parse failure
        if step.get("tool_call") is None and not step.get("direct_code_extracted"):
            n += 1
    return n


def _count_no_tool_calls(syntax_agent: dict | None) -> int:
    """Count rounds where the model emitted no parsed tool call at all
    (not even a malformed one that fell through to extraction)."""
    if not syntax_agent or not isinstance(syntax_agent, dict):
        return 0
    steps = syntax_agent.get("steps", [])
    if not steps:
        return 0
    n = 0
    for step in steps:
        if step.get("tool_call") is None:
            n += 1
    return n


def _count_rewrite_rounds(syntax_agent: dict | None) -> int:
    if not syntax_agent or not isinstance(syntax_agent, dict):
        return 0
    return syntax_agent.get("rewrite_rounds", 0)


def summarize_run(run_dir: str):
    """Print a summary of a single run directory."""
    meta = _load_run_meta(run_dir)
    puzzle_files = sorted(
        f for f in glob.glob(os.path.join(run_dir, "*.json")) if _is_puzzle_file(f)
    )

    if not puzzle_files:
        print(f"No puzzle files found in {run_dir}")
        return

    # Header
    if meta:
        print(f"Run:     {meta.get('run_name', os.path.basename(run_dir))}")
        print(f"Seed:    {meta.get('seed', '?')}")
        print(f"Puzzles: {meta.get('n_puzzles', len(puzzle_files))}")
        print(f"Args:    {meta.get('args', {})}")
    else:
        print(f"Run:     {os.path.basename(run_dir)} (no _run_meta.json)")

    print()
    print(f"{'Puzzle':<22} {'Syntax':<10} {'Stop':<14} {'Steps':<6} {'RW':<4} {'PF':<4} {'NoTC':<4} {'Solved':<7}")
    print("-" * 75)

    n_syntax_clean = 0
    n_solved = 0
    total_parse_failures = 0
    total_no_tool_calls = 0
    total_rewrite_rounds = 0
    n_agent_triggered = 0

    for fpath in puzzle_files:
        with open(fpath) as f:
            data = json.load(f)

        pid = data.get("puzzle_id", os.path.basename(fpath).replace(".json", ""))
        sa = data.get("syntax_agent")

        if sa and sa.get("triggered"):
            n_agent_triggered += 1
            syntax_label = "agent" if sa.get("triggered") else "ok"
            stop = sa.get("stop_reason", "?")
            steps = len(sa.get("steps", []))
            rw = sa.get("rewrite_rounds", 0)
            pf = _count_parse_failures(sa)
            total_parse_failures += pf
            ntc = _count_no_tool_calls(sa)
            total_no_tool_calls += ntc
            total_rewrite_rounds += rw
        elif sa and not sa.get("triggered"):
            syntax_label = "quick_fixed" if sa.get("quick_fix_applied", 0) > 0 else "clean"
            stop = sa.get("stop_reason", "no_agent")
            steps = 0
            rw = 0
            pf = 0
            ntc = 0
            n_syntax_clean += 1
        else:
            syntax_label = "no_record"
            stop = "?"
            steps = 0
            rw = 0
            pf = 0
            ntc = 0

        solved = "YES" if data.get("final_correct") else "no"
        if data.get("final_correct"):
            n_solved += 1

        print(f"{pid:<22} {syntax_label:<10} {stop:<14} {steps:<6} {rw:<4} {pf:<4} {ntc:<4} {solved:<7}")

    print("-" * 75)
    print(
        f"{'TOTAL':<22} {'':<10} {'':<14}"
        f"{'':<6} {total_rewrite_rounds:<4} {total_parse_failures:<4} {total_no_tool_calls:<4}"
        f" {n_solved}/{len(puzzle_files)}"
    )
    print()
    print(f"  Syntax clean (no agent):   {n_syntax_clean}")
    print(f"  Agent triggered:            {n_agent_triggered}")
    print(f"  Total rewrite rounds:       {total_rewrite_rounds}")
    print(f"  Total parse failures:       {total_parse_failures}")
    print(f"  Total no-tool-call rounds:  {total_no_tool_calls}")
    print(f"  Solved:                     {n_solved}/{len(puzzle_files)} ({n_solved/len(puzzle_files)*100:.1f}%)")


def main():
    parser = argparse.ArgumentParser(
        description="Summarize asp-arc multi-seed experiment results."
    )
    parser.add_argument(
        "run_dirs",
        nargs="+",
        help="One or more result directories (e.g. results/seed11_*)",
    )
    args = parser.parse_args()

    for run_dir in args.run_dirs:
        if not os.path.isdir(run_dir):
            # Expand glob
            matches = sorted(glob.glob(run_dir))
            if not matches:
                print(f"No matches for {run_dir}")
                continue
            for m in matches:
                if os.path.isdir(m):
                    summarize_run(m)
                    print()
        else:
            summarize_run(run_dir)
            print()


if __name__ == "__main__":
    main()
