"""
Comprehensive syntax error analysis across multi-seed runs.

Usage:
    python scripts/analyze_syntax.py <result_dir1> <result_dir2> ...
"""
import json
import os
import sys
import re
from collections import Counter, defaultdict

ERROR_PATTERNS = [
    (r"error: syntax error, unexpected \:", "unexpected_colon"),
    (r"error: syntax error, unexpected ','", "unexpected_comma"),
    (r"error: syntax error, unexpected '\)'", "unexpected_rparen"),
    (r"error: syntax error, unexpected '\('", "unexpected_lparen"),
    (r"error: syntax error, unexpected ([A-Z])", "unexpected_upper_var"),
    (r"error: syntax error, unexpected ([a-z])", "unexpected_lower_var"),
    (r"error: syntax error, unexpected \#", "unexpected_hash"),
    (r"error: syntax error, unexpected ([0-9])", "unexpected_number"),
    (r"error: syntax error, unexpected \$", "unexpected_dollar"),
    (r"error: syntax error, unexpected \&", "unexpected_ampersand"),
    (r"error: syntax error, unexpected \|", "unexpected_pipe"),
    (r"error: syntax error, unexpected \?", "unexpected_question"),
    (r"error: syntax error, unexpected \\", "unexpected_backslash"),
    (r"error: syntax error, unexpected \!", "unexpected_bang"),
    (r"error: syntax error, unexpected end of file", "unexpected_eof"),
    (r"error: syntax error, unexpected ([^\n]+)", "unexpected_other"),
    (r"error: (.*not defined|.*no such)", "undefined_symbol"),
    (r"error: (.*type error|.*type mismatch)", "type_error"),
    (r"error: (.*cyclic|.*cycle|.*circular)", "cyclic_dependency"),
    (r"error: (.*safe|.*unsafe|.*free variable)", "unsafe_variable"),
    (r"error: (.*multiple definition|.*redefinition)", "redefinition"),
    (r"info: tuple too long", "tuple_too_long"),
    (r"info: (.*ignored|.*deprecated)", "ignored_deprecated"),
]

def classify_error(msg):
    for pattern, label in ERROR_PATTERNS:
        if re.search(pattern, msg):
            return label
    return "other"


def main(dirs):
    all_data = {
        "per_strategy": defaultdict(lambda: {"total": 0, "clean": 0, "broken": 0, "fixed": 0, "stays_broken": 0}),
        "error_before": Counter(),
        "error_after": Counter(),
        "rounds": Counter(),
        "fix_accuracy": {"total": 0, "n_correct": 0},
    }

    for d in dirs:
        puzzles = sorted(f for f in os.listdir(d) if f.endswith('.json') and f != '_run_meta.json')
        for pf in puzzles:
            data = json.load(open(os.path.join(d, pf)))
            for c in data['candidates']:
                idx = c["idx"]
                all_data["per_strategy"][idx]["total"] += 1
                stages = c.get("syntax_fix_stages", [])
                details = c.get("syntax_fix_details", [])

                # Determine final outcome
                if "rewrite_partial" in stages:
                    outcome = "stays_broken"
                elif "rewrite" in stages:
                    outcome = "fixed"
                else:
                    outcome = "clean"
                all_data["per_strategy"][idx][outcome] += 1

                # Track syntax state
                if outcome == "clean":
                    all_data["per_strategy"][idx]["clean"] += 1
                else:
                    all_data["per_strategy"][idx]["broken"] += 1

                # Collect error messages from rewrite rounds
                for det in details:
                    if det.get("stage", "").startswith("rewrite"):
                        rounds = det.get("rounds", [])
                        for r in rounds:
                            err_before = r.get("syntax_error_before", "")
                            if err_before:
                                all_data["error_before"][classify_error(err_before)] += 1
                            err_after = r.get("syntax_error_after", "")
                            if err_after:
                                all_data["error_after"][classify_error(err_after)] += 1

                        n_rounds = det.get("n_rounds", 0)
                        all_data["rounds"][n_rounds] += 1

                # Fix accuracy: of LLM-fixed candidates, how many pass training?
                if outcome == "fixed":
                    all_data["fix_accuracy"]["total"] += 1
                    n_correct = c.get("n_correct", 0)
                    if n_correct >= c.get("total_examples", 1):
                        all_data["fix_accuracy"]["n_correct"] += 1
                    avg_acc = c.get("avg_accuracy", 0)
                    if avg_acc > 0:
                        all_data["fix_accuracy"]["n_correct"] += 1  # rough

    # ── Print results ──
    print("=" * 60)
    print("1. STRATEGY-LEVEL SYNTAX CLEANLINESS")
    print("=" * 60)
    print(f"{'Strategy':<10} {'Total':>6} {'Clean':>6} {'Broken':>8} {'Fixed':>6} {'Stays':>7} {'Clean%':>7} {'Fix%':>7}")
    print("-" * 60)
    for sid in sorted(all_data["per_strategy"]):
        s = all_data["per_strategy"][sid]
        clean_pct = s["clean"] / s["total"] * 100 if s["total"] else 0
        fix_pct = s["fixed"] / (s["broken"]) * 100 if s["broken"] else 0
        print(f"{sid:<10} {s['total']:>6} {s['clean']:>6} {s['broken']:>8} {s['fixed']:>6} {s['stays_broken']:>7} {clean_pct:>6.1f}% {fix_pct:>6.1f}%")

    print()
    print("=" * 60)
    print("2. ERROR TYPE TAXONOMY (BEFORE LLM REWRITE)")
    print("=" * 60)
    total_before = sum(all_data["error_before"].values())
    for label, cnt in all_data["error_before"].most_common():
        print(f"  {label:<30} {cnt:>5}  ({cnt/total_before*100:>5.1f}%)")
    print(f"  {'TOTAL':<30} {total_before:>5}")

    print()
    print("=" * 60)
    print("3. ERROR TYPE TAXONOMY (AFTER LAST LLM REWRITE — PERSISTENT ERRORS)")
    print("=" * 60)
    total_after = sum(all_data["error_after"].values())
    for label, cnt in all_data["error_after"].most_common():
        print(f"  {label:<30} {cnt:>5}  ({cnt/total_after*100:>5.1f}%)")
    print(f"  {'TOTAL':<30} {total_after:>5}")

    print()
    print("=" * 60)
    print("4. FIX ROUND DISTRIBUTION")
    print("=" * 60)
    for r in sorted(all_data["rounds"]):
        print(f"  {r} round(s): {all_data['rounds'][r]} candidates")
    print(f"  Total rewrite attempts: {sum(all_data['rounds'].values())}")

    print()
    print("=" * 60)
    print("5. SYNTAX-FIXED BUT SEMANTICALLY WRONG")
    print("=" * 60)
    fa = all_data["fix_accuracy"]
    print(f"  LLM-fixed candidates:                {fa['total']}")
    print(f"  LLM-fixed with >0 training correct:  {fa['n_correct']}")


if __name__ == "__main__":
    dirs = sys.argv[1:] if len(sys.argv) > 1 else ["."]
    main(dirs)
