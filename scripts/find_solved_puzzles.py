#!/usr/bin/env python3
"""Scan outputs/ JSONL files and write the set of solved puzzles.

A puzzle is "solved" if any record for that (puzzle_name1, puzzle_name2)
pair has validation.passed == true. Reads every batch_*.jsonl in outputs/
EXCEPT those whose filename contains "nemo". Writes one "<p1>\\t<p2>" line
per solved puzzle to solved_puzzles.tsv at the repo root, overwriting any
prior version.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = REPO_ROOT / "outputs"
OUTPUT_FILE = REPO_ROOT / "solved_puzzles.tsv"


def main():
    files = sorted(
        p for p in OUTPUTS_DIR.glob("batch_*.jsonl") if "nemo" not in p.name
    )
    if not files:
        raise SystemExit(f"No batch_*.jsonl files found in {OUTPUTS_DIR}")

    print(f"Scanning {len(files)} file(s) in {OUTPUTS_DIR}")
    solved: set[tuple[str, str]] = set()

    for path in files:
        n_records = 0
        new_in_file = 0
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                n_records += 1
                v = record.get("validation")
                if v and v.get("passed"):
                    key = (record["puzzle_name1"], record["puzzle_name2"])
                    if key not in solved:
                        solved.add(key)
                        new_in_file += 1
        print(f"  {path.name}: {n_records} records, +{new_in_file} newly-solved")

    with open(OUTPUT_FILE, "w") as f:
        for p1, p2 in sorted(solved):
            f.write(f"{p1}\t{p2}\n")

    print(f"\nWrote {len(solved)} unique solved puzzles to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
