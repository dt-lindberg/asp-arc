"""Offline syntax-repair agent runner.

Walks every JSON under ~/Projects/asp-arc/src/audit/*/*.json, collects
(program, clingo_errors) pairs for programs that produced Clingo errors,
deduplicates by program text, and runs the syntax-repair agent on them.

Phase 1 (default): 5–10 puzzles, transcripts saved to
  results/syntax_repair_runs/<puzzle_id>.json

Phase 2 (--full): all broken programs in the corpus.

The run ID is the audit directory name of the first puzzle found, or
can be overridden with --run-id.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

AUDIT_GLOB = os.path.expanduser("~/Projects/asp-arc/src/audit/*/*.json")
RESULTS_DIR = ROOT / "results" / "syntax_repair_runs"


def collect_broken_programs(
    max_puzzles: int | None = None,
    audit_run: str | None = None,
) -> list[dict]:
    """Collect (program, clingo_errors) pairs from the audit corpus.

    Returns list of {"puzzle_id": ..., "source_audit": ..., "program": ..., "errors": [...]}
    Deduplicated by program text.
    """
    import re as _re  # noqa: F401 - used below in comprehension, keeps linters happy

    pattern = AUDIT_GLOB
    if audit_run:
        pattern = os.path.expanduser(
            f"~/Projects/asp-arc/src/audit/{audit_run}/*.json"
        )

    files = sorted(glob.glob(pattern), reverse=True)  # most recent first
    seen_progs: set[str] = set()
    puzzles: list[dict] = []

    for f in files:
        try:
            data = json.load(open(f))
        except Exception:
            continue

        puzzle_id = data.get("puzzle_id", Path(f).stem)
        items = []
        if "steps" in data and isinstance(data["steps"], dict):
            init = data["steps"].get("initial")
            if init:
                items.append(("initial", init))
        items.extend(("refinement", r) for r in (data.get("refinements") or []))

        for _source, it in items:
            prog = (it.get("program") or "").strip()
            if not prog or prog in seen_progs:
                continue
            verifs = it.get("train_verifications") or []
            clingo_error = None
            for v in verifs:
                if v.get("status") == "clingo_error":
                    clingo_error = v.get("clingo_errors", "")
                    break
            if clingo_error is None:
                continue

            seen_progs.add(prog)

            # ASCII-normalize the program (match pipeline behavior)
            prog_normalized = prog.encode("ascii", errors="replace").decode("ascii")
            # Remove null bytes that "?" replacement can cause weird interactions
            prog_normalized = prog_normalized.replace("?", "")

            # Parse Clingo error string into lines
            error_lines = [
                line.strip()
                for line in clingo_error.split("\n")
                if line.strip()
            ]
            if not error_lines:
                error_lines = ["(clingo reported errors but message was empty)"]

            puzzles.append(
                {
                    "puzzle_id": f"{puzzle_id}",
                    "source_audit": f,
                    "program": prog_normalized,
                    "errors": error_lines,
                }
            )

        if max_puzzles and len(puzzles) >= max_puzzles:
            break

    return puzzles


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the syntax-repair agent on broken audit programs."
    )
    parser.add_argument(
        "--num",
        type=int,
        default=5,
        help="Number of puzzles to process (default: 5 for Phase 1).",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Process all broken programs (Phase 2).",
    )
    parser.add_argument(
        "--audit-run",
        default=None,
        help="Specific audit run directory name (e.g. '20260426_123318').",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=132,
        help="Random seed used for both puzzle sampling and LLM sampling.",
    )
    parser.add_argument(
        "--run-name",
        default=None,
        help="Subdirectory under results/syntax_repair_runs/ to write transcripts. "
             "Defaults to seed<seed>.",
    )
    parser.add_argument(
        "--all-audits",
        action="store_true",
        help="Sample puzzles from every audit run (default: only --audit-run).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only collect and report programs; do not call the LLM.",
    )
    args = parser.parse_args(argv)

    audit_run = args.audit_run

    # When --all-audits is set, we collect from every audit dir and let the
    # seed pick which N puzzles to use.  Otherwise default to most recent.
    if args.all_audits:
        audit_run = None
    elif audit_run is None:
        runs = sorted(glob.glob(os.path.expanduser("~/Projects/asp-arc/src/audit/*/")))
        if runs:
            audit_run = os.path.basename(runs[-1].rstrip("/"))

    print(f"Audit run: {audit_run if audit_run else '<all>'}")
    # Always collect the full pool; seed-based sampling picks the subset.
    puzzles = collect_broken_programs(max_puzzles=None, audit_run=audit_run)
    print(f"Collected {len(puzzles)} unique broken programs")

    # Seed-based random sampling so different seeds explore different puzzles.
    if not args.full and args.num and len(puzzles) > args.num:
        rng = random.Random(args.seed)
        puzzles = rng.sample(puzzles, args.num)
        print(f"Sampled {len(puzzles)} puzzles using seed={args.seed}")

    if args.dry_run:
        for i, p in enumerate(puzzles):
            print(f"\n--- Puzzle {i + 1}: {p['puzzle_id']} ---")
            print(f"Program ({len(p['program'])} chars):")
            print(p["program"][:500])
            print(f"\nErrors:")
            for e in p["errors"]:
                print(f"  {e[:120]}")
        return

    if not puzzles:
        print("No broken programs found.")
        return

    # Run the agent
    from agent.syntax_repair_agent import SyntaxRepairAgent
    from config.config_agent import THINKING

    print(f"THINKING={'ON' if THINKING else 'OFF'} (set via AGENT_THINKING env var)")

    agent = SyntaxRepairAgent(seed=args.seed)
    transcripts = agent.run(puzzles)

    # Save transcripts under a per-run subdir to keep parallel jobs from clashing.
    run_name = args.run_name or f"seed{args.seed}"
    out_dir = RESULTS_DIR / run_name
    out_dir.mkdir(parents=True, exist_ok=True)

    # Persist the run config alongside the transcripts.
    meta = {
        "seed": args.seed,
        "num_requested": args.num if not args.full else None,
        "num_processed": len(transcripts),
        "audit_run": audit_run,
        "all_audits": args.all_audits,
        "thinking": THINKING,
        "run_name": run_name,
    }
    (out_dir / "_run_meta.json").write_text(json.dumps(meta, indent=2))

    for t in transcripts:
        path = out_dir / f"{t.puzzle_id}.json"
        t.to_json(path)
        print(f"  [{t.halt_reason}] {t.puzzle_id} -> {path}")

    # Quick summary
    fixed = sum(1 for t in transcripts if t.halt_reason == "fixed")
    print(f"\nSummary [{run_name}]: {fixed}/{len(transcripts)} fixed")


if __name__ == "__main__":
    main()
