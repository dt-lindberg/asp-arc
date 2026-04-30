"""
Qualitative analysis: what happens to programs the syntax fixer can and cannot fix.

Outputs structured data and concrete examples to stdout.
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

ERROR_PATTERNS = [
    (r"error: syntax error, unexpected \:", "unexpected_colon"),
    (r"error: syntax error, unexpected ','", "unexpected_comma"),
    (r"error.*unexpected '\)'", "unexpected_rparen"),
    (r"error.*unexpected '\('", "unexpected_lparen"),
    (r"error.*unexpected ([A-Z])(?!\))", "unexpected_upper_var"),
    (r"error.*unexpected ([a-z])(?!\))", "unexpected_lower_var"),
    (r"error.*unexpected \#", "unexpected_hash"),
    (r"error.*unexpected ([0-9])", "unexpected_number"),
    (r"error.*unexpected \$", "unexpected_dollar"),
    (r"error.*unexpected \&", "unexpected_ampersand"),
    (r"error.*unexpected \|", "unexpected_pipe"),
    (r"error.*unexpected \?", "unexpected_question"),
    (r"error.*unexpected \\", "unexpected_backslash"),
    (r"error.*unexpected \!", "unexpected_bang"),
    (r"error.*unexpected end of file", "unexpected_eof"),
    (r"error.*unexpected ([^\n]+)", "unexpected_other"),
    (r"(not defined|no such)", "undefined_symbol"),
    (r"(type error|type mismatch)", "type_error"),
    (r"(cyclic|cycle|circular)", "cyclic_dependency"),
    (r"(safe|unsafe|free variable)", "unsafe_variable"),
    (r"(multiple definition|redefinition)", "redefinition"),
    (r"info: tuple too long", "tuple_too_long"),
]

def classify_error(msg):
    for pattern, label in ERROR_PATTERNS:
        if re.search(pattern, msg):
            return label
    return "other"

def first_n_lines(text, n=3):
    """Extract first n non-empty lines for display."""
    lines = [l for l in text.split("\n") if l.strip()]
    return "\n".join(lines[:n])

def main():
    dirs = sys.argv[1:] if len(sys.argv) > 1 else []
    if not dirs:
        print("Usage: python analyze_qualitative.py <result_dir1> ...")
        sys.exit(1)

    # Collect structured data
    stuck_candidates = []       # rewrite_partial
    fixed_candidates = []       # rewrite (fully fixed)
    agent_cases = []            # syntax_agent triggered
    clean_candidates = []       # no rewrite needed

    for d in dirs:
        puzzles = sorted(f for f in os.listdir(d) if f.endswith(".json") and f != "_run_meta.json")
        for pf in puzzles:
            data = json.load(open(os.path.join(d, pf)))
            puzzle_id = data["puzzle_id"]
            seed = data["seed"]

            for c in data["candidates"]:
                stages = c.get("syntax_fix_stages", [])
                details = c.get("syntax_fix_details", [])
                sid = c["idx"]

                record = {
                    "puzzle_id": puzzle_id,
                    "seed": seed,
                    "strategy": sid,
                    "stages": stages,
                    "details": details,
                    "program_final": c.get("program_final", ""),
                    "n_correct": c.get("n_correct", 0),
                    "avg_accuracy": c.get("avg_accuracy", 0),
                }

                if "rewrite_partial" in stages:
                    # Stuck — extract round-by-round info
                    rounds_info = []
                    for det in details:
                        if det.get("stage", "").startswith("rewrite"):
                            for r in det.get("rounds", []):
                                rounds_info.append({
                                    "round": r.get("round", 0),
                                    "err_before": r.get("syntax_error_before", ""),
                                    "err_after": r.get("syntax_error_after", ""),
                                    "type_before": classify_error(r.get("syntax_error_before", "")),
                                    "type_after": classify_error(r.get("syntax_error_after", "")),
                                    "program_before": r.get("program_before", ""),
                                    "program_after": r.get("program_after", ""),
                                })
                    record["rounds_info"] = rounds_info
                    stuck_candidates.append(record)

                elif "rewrite" in stages:
                    # Fixed
                    rounds_info = []
                    for det in details:
                        if det.get("stage", "").startswith("rewrite"):
                            for r in det.get("rounds", []):
                                rounds_info.append({
                                    "round": r.get("round", 0),
                                    "err_before": r.get("syntax_error_before", ""),
                                    "type_before": classify_error(r.get("syntax_error_before", "")),
                                    "program_before": r.get("program_before", ""),
                                    "program_after": r.get("program_after", ""),
                                })
                    record["rounds_info"] = rounds_info
                    fixed_candidates.append(record)

                else:
                    clean_candidates.append(record)

            # Puzzle-level syntax agent
            sa = data.get("syntax_agent", {})
            if sa.get("triggered", False):
                agent_cases.append({
                    "puzzle_id": puzzle_id,
                    "seed": seed,
                    "syntax_agent": sa,
                    "refinements": data.get("refinements", []),
                    "final_correct": data.get("final_correct", False),
                })

    # ── ANALYSIS 1: ERROR TRAJECTORIES FOR STUCK CANDIDATES ──
    print("=" * 70)
    print("ANALYSIS 1: ERROR TRAJECTORIES (stuck candidates, 3 rounds)")
    print("=" * 70)

    trajectories = Counter()  # (t1, t2, t3) → count
    for rec in stuck_candidates:
        ri = rec["rounds_info"]
        if len(ri) >= 3:
            types = tuple(r["type_before"] for r in ri[:3])
            trajectories[types] += 1
        elif len(ri) == 2:
            types = tuple(r["type_before"] for r in ri[:2] + (None,))
            trajectories[types] += 1

    print(f"\nTotal stuck candidates: {len(stuck_candidates)}")
    print(f"\nTop error trajectories (type_round1 → type_round2 → type_round3):")
    for traj, cnt in trajectories.most_common(20):
        print(f"  {traj[0]:<25} → {traj[1]:<25} → {traj[2]:<25} : {cnt:>3}")

    # Same-error-stuck (same type all 3 rounds)
    same_stuck = sum(cnt for traj, cnt in trajectories.items()
                     if traj[0] == traj[1])
    print(f"\nSame error all 3 rounds: {same_stuck} ({same_stuck/len(stuck_candidates)*100:.1f}%)")

    # Degrading (different error in each round, getting more severe)
    changing = sum(cnt for traj, cnt in trajectories.items()
                   if len(set(traj)) > 1)
    print(f"Error type changes across rounds: {changing} ({changing/len(stuck_candidates)*100:.1f}%)")

    # ── ANALYSIS 2: ROUND-BY-ROUND ERROR TYPE SHIFTS ──
    print("\n" + "=" * 70)
    print("ANALYSIS 2: ERROR TYPE TRANSITION MATRIX")
    print("=" * 70)
    # Count transitions from round N to round N+1
    transitions = Counter()
    for rec in stuck_candidates:
        ri = rec["rounds_info"]
        for i in range(len(ri) - 1):
            t_before = ri[i]["type_before"]
            t_after = ri[i+1]["type_before"]
            transitions[(t_before, t_after)] += 1

    print(f"\nTop transitions (Round N → Round N+1):")
    for (from_t, to_t), cnt in transitions.most_common(15):
        print(f"  {from_t:<25} → {to_t:<25} : {cnt:>3}")

    # ── ANALYSIS 3: CONCRETE STUCK EXAMPLE ──
    print("\n" + "=" * 70)
    print("EXAMPLE 1: STUCK — SAME ERROR ALL 3 ROUNDS")
    print("=" * 70)
    for rec in stuck_candidates:
        ri = rec["rounds_info"]
        if len(ri) >= 3:
            types = [r["type_before"] for r in ri[:3]]
            if types[0] == types[1] == types[2]:
                print(f"\nPuzzle: {rec['puzzle_id']}, Seed: {rec['seed']}, Strategy: {rec['strategy']}")
                print(f"Error type: {types[0]}")
                for r in ri[:3]:
                    print(f"\n  Round {r['round']}:")
                    err = r["err_before"][:200]
                    print(f"  Error: {err}")
                    prog = r["program_before"]
                    print(f"  Program ({len(prog)} chars):")
                    for line in prog.split("\n")[:8]:
                        print(f"    {line}")
                    if len(prog.split("\n")) > 8:
                        print(f"    ... ({len(prog.split('\n')) - 8} more lines)")
                break

    # ── EXAMPLE 2: STUCK — CHANGING ERRORS ──
    print("\n" + "=" * 70)
    print("EXAMPLE 2: STUCK — ERROR TYPE CHANGES")
    print("=" * 70)
    for rec in stuck_candidates:
        ri = rec["rounds_info"]
        if len(ri) >= 3:
            types = [r["type_before"] for r in ri[:3]]
            if len(set(types)) > 1:
                print(f"\nPuzzle: {rec['puzzle_id']}, Seed: {rec['seed']}, Strategy: {rec['strategy']}")
                print(f"Error trajectory: {types[0]} → {types[1]} → {types[2]}")
                for r in ri[:3]:
                    print(f"\n  Round {r['round']} ({r['type_before']}):")
                    print(f"  Error: {r['err_before'][:200]}")
                    prog = r["program_before"]
                    for line in prog.split("\n")[:6]:
                        print(f"    {line}")
                    if len(prog.split("\n")) > 6:
                        print(f"    ...")
                break

    # ── EXAMPLE 3: STUCK — LLM INTRODUCES NEW ERRORS ──
    print("\n" + "=" * 70)
    print("EXAMPLE 3: STUCK — LLM INTRODUCES NEW ERRORS")
    print("=" * 70)
    for rec in stuck_candidates:
        ri = rec["rounds_info"]
        if len(ri) >= 3:
            types_before = [r["type_before"] for r in ri[:3]]
            types_after = [r.get("type_after", "none") for r in ri[:3]]
            # Check if after-round errors differ from before-round (LLM made things worse)
            for i, r in enumerate(ri[:3]):
                tb = r["type_before"]
                ta = r.get("type_after", "")
                if tb != ta and ta:
                    print(f"\nPuzzle: {rec['puzzle_id']}, Seed: {rec['seed']}, Strategy: {rec['strategy']}")
                    print(f"Round {r['round']}: error changed {tb} → {ta} after LLM rewrite")
                    print(f"  Before error: {r['err_before'][:200]}")
                    print(f"  LLM output had: {r.get('err_after', '')[:200]}")
                    print(f"  Program before ({len(r['program_before'])} chars):")
                    for line in r["program_before"].split("\n")[:5]:
                        print(f"    {line}")
                    print(f"  Program after ({len(r.get('program_after', ''))} chars):")
                    for line in r.get("program_after", "").split("\n")[:5]:
                        print(f"    {line}")
                    break
            else:
                continue
            break

    # ── ANALYSIS 4: SUCCESS PATTERNS ──
    print("\n" + "=" * 70)
    print("ANALYSIS 4: SUCCESS PATTERNS (fixed candidates)")
    print("=" * 70)
    print(f"\nTotal fixed candidates: {len(fixed_candidates)}")

    # Count how many rounds to fix
    rounds_to_fix = Counter()
    for rec in fixed_candidates:
        rounds_to_fix[len(rec["rounds_info"])] += 1
    print(f"Rounds to fix:")
    for r in sorted(rounds_to_fix):
        print(f"  {r} round(s): {rounds_to_fix[r]}")

    # ── EXAMPLE 4: FIXED IN 1 ROUND ──
    print("\n" + "=" * 70)
    print("EXAMPLE 4: FIXED IN 1 ROUND")
    print("=" * 70)
    for rec in fixed_candidates:
        if len(rec["rounds_info"]) == 1:
            r = rec["rounds_info"][0]
            print(f"\nPuzzle: {rec['puzzle_id']}, Seed: {rec['seed']}, Strategy: {rec['strategy']}")
            print(f"Error type: {r['type_before']}")
            print(f"Error: {r['err_before'][:200]}")
            print(f"\n  Program BEFORE ({len(r['program_before'])} chars):")
            for line in r["program_before"].split("\n")[:8]:
                print(f"    {line}")
            print(f"\n  Program AFTER ({len(r.get('program_after', ''))} chars):")
            for line in r.get("program_after", "").split("\n")[:8]:
                print(f"    {line}")
            break

    # ── EXAMPLE 5: FIXED IN 3 ROUNDS ──
    print("\n" + "=" * 70)
    print("EXAMPLE 5: FIXED IN 3 ROUNDS")
    print("=" * 70)
    for rec in fixed_candidates:
        if len(rec["rounds_info"]) == 3:
            print(f"\nPuzzle: {rec['puzzle_id']}, Seed: {rec['seed']}, Strategy: {rec['strategy']}")
            for r in rec["rounds_info"]:
                print(f"\n  Round {r['round']} ({r['type_before']}):")
                print(f"  Error: {r['err_before'][:200]}")
                prog = r["program_before"]
                for line in prog.split("\n")[:5]:
                    print(f"    {line}")
            print(f"\n  FINAL program ({len(rec['program_final'])} chars):")
            for line in rec["program_final"].split("\n")[:5]:
                print(f"    {line}")
            print(f"  Training correct: {rec['n_correct']}/{rec.get('total_examples', '?')}")
            break

    # ── ANALYSIS 5: PUZZLE-LEVEL SYNTAX AGENT ──
    print("\n" + "=" * 70)
    print("ANALYSIS 5: PUZZLE-LEVEL SYNTAX AGENT DEEP DIVE")
    print("=" * 70)
    print(f"\nTotal agent-triggered puzzles: {len(agent_cases)}")

    for ac in agent_cases:
        sa = ac["syntax_agent"]
        print(f"\n{'─' * 60}")
        print(f"Puzzle: {ac['puzzle_id']} | Seed: {ac['seed']} | Solved: {ac['final_correct']}")
        print(f"Stop reason: {sa.get('stop_reason', 'N/A')}")
        print(f"Initial error: {sa.get('initial_error', 'N/A')[:200]}")

        steps = sa.get("steps", [])
        print(f"  Steps ({len(steps)}):")
        for s in steps:
            tc = s.get("tool_call", "")
            if isinstance(tc, dict):
                tc_name = tc.get("name", "?")
            else:
                tc_name = str(tc)[:40]
            direct_code = s.get("direct_code_extracted", False)
            pb = s.get("program_before", "")
            pa = s.get("program_after", "")
            se = s.get("syntax_error_before", "")
            print(f"    Round {s.get('round', '?')}: tool={tc_name} "
                  f"direct_code={direct_code} "
                  f"prog={len(pb)}→{len(pa)} "
                  f"err={se[:80] if se else 'none'}")

    # ── ANALYSIS 6: PROGRAM SIZE CHANGE ──
    print("\n" + "=" * 70)
    print("ANALYSIS 6: PROGRAM SIZE CHANGE (BEFORE → AFTER REWRITE)")
    print("=" * 70)
    sizes_before = []
    sizes_after = []
    for rec in stuck_candidates + fixed_candidates:
        for r in rec.get("rounds_info", []):
            sizes_before.append(len(r.get("program_before", "")))
            sizes_after.append(len(r.get("program_after", "")))

    if sizes_before:
        avg_before = sum(sizes_before) / len(sizes_before)
        avg_after = sum(sizes_after) / len(sizes_after)
        print(f"  Avg program size before: {avg_before:.0f} chars")
        print(f"  Avg program size after:  {avg_after:.0f} chars")
        print(f"  Avg change:              {avg_after - avg_before:+.0f} chars ({(avg_after/avg_before-1)*100:+.1f}%)")

    # ── SUMMARY STATS ──
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Total candidates checked:   {len(clean_candidates) + len(fixed_candidates) + len(stuck_candidates)}")
    print(f"  Clean (no rewrite needed):  {len(clean_candidates)}")
    print(f"  Fixed by rewrite:           {len(fixed_candidates)}")
    print(f"  Stuck after 3 rounds:       {len(stuck_candidates)}")
    print(f"  Same error all 3 rounds:    {same_stuck} ({same_stuck/len(stuck_candidates)*100:.1f}% of stuck)")
    print(f"  Puzzle agent triggered:     {len(agent_cases)}")
    print(f"  Agent success rate:         {sum(1 for ac in agent_cases if ac['final_correct'])}/{len(agent_cases)}")

if __name__ == "__main__":
    main()
