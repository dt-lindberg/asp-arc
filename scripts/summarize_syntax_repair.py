"""Quick summary of syntax-repair agent transcripts.

Usage:
    python scripts/summarize_syntax_repair.py [--dir results/syntax_repair_runs]
"""

import argparse
import json
import glob
import os
from pathlib import Path


def summarize(transcript_dir: str):
    files = sorted(glob.glob(os.path.join(transcript_dir, "*.json")))
    if not files:
        print("No transcripts found.")
        return

    print(f"Found {len(files)} transcript(s)\n")
    print(f"{'Puzzle':<20} {'Halt':<15} {'Rounds':<8} {'Calls':<8} {'Avg/round':<12} {'Prompt tok':<12} {'Comp tok':<12}")
    print("-" * 87)

    total_fixed = 0
    for f in files:
        data = json.load(open(f))
        pid = data["puzzle_id"]
        halt = data["halt_reason"]
        rounds = data["rounds"]
        n_rounds = len(rounds)
        n_calls = sum(len(r.get("tool_calls", [])) for r in rounds)
        avg_per = n_calls / n_rounds if n_rounds > 0 else 0
        prompt_toks = sum(r.get("prompt_tokens", 0) for r in rounds)
        comp_toks = sum(r.get("completion_tokens", 0) for r in rounds)

        if halt == "fixed":
            total_fixed += 1

        print(
            f"{pid:<20} {halt:<15} {n_rounds:<8} {n_calls:<8} "
            f"{avg_per:<12.1f} {prompt_toks:<12} {comp_toks:<12}"
        )

    print(f"\nFix rate: {total_fixed}/{len(files)} ({total_fixed / len(files) * 100:.1f}%)")

    # Per-round detail
    print(f"\n{'=' * 60}")
    for f in files:
        data = json.load(open(f))
        pid = data["puzzle_id"]
        print(f"\n--- {pid} ---")
        for r in data["rounds"]:
            calls = r.get("tool_calls", [])
            failures = r.get("parse_failures", [])
            call_names = [c["name"] for c in calls]
            errors = r.get("errors_after", [])
            print(f"  Round {r['round_idx']}: {len(calls)} calls {call_names}, "
                  f"{len(failures)} parse failures, "
                  f"errors: {len(errors)}")
            if failures:
                for pf in failures:
                    print(f"    Parse failure: {pf[:120]}")
            if errors:
                for e in errors[:3]:
                    print(f"    Error: {e[:120]}")

            # Show thinking snippet
            thinking = r.get("thinking", "")
            if thinking:
                print(f"    Thinking: {thinking[:200]}...")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default="results/syntax_repair_runs")
    args = p.parse_args()
    summarize(args.dir)
