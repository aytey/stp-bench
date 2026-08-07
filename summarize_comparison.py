#!/usr/bin/env python3
"""
Summarize a build comparison run.

Produces two summaries:
  1. Main pass only (30s timeout)
  2. Combined: revalidation results replace main-pass results for
     any (file, solver) pair that was revalidated

Usage:
    python3 summarize_comparison.py build_comparison.csv build_comparison_revalidation.csv
"""

import argparse
import os
import statistics
import sys
from collections import Counter

from benchlib import (
    BOLD, RED, GREEN, YELLOW, CYAN, MAGENTA, RST,
    load_medians, load_combined, pair_files, shorten, extract_logic, score_table,
)


def summarize_by_theory(paired, solver_a, solver_b):
    """Print per-solver summary grouped by theory."""
    by_logic = {}
    for path, (a, b) in paired.items():
        logic = extract_logic(path)
        by_logic.setdefault(logic, {})[path] = (a, b)

    for logic in sorted(by_logic.keys()):
        files = by_logic[logic]
        print(f"{BOLD}{'═' * 78}{RST}")
        print(f"{BOLD}  {logic}{RST}  ({len(files):,} files)")
        print(f"{'═' * 78}")

        for name in [solver_a, solver_b]:
            vals = [v[0] if name == solver_a else v[1] for v in files.values()]
            c = Counter(v["answer"] for v in vals)
            solved = [v["elapsed"] for v in vals if v["answer"] in ("sat", "unsat")]
            avg = statistics.mean(solved) if solved else 0
            med = statistics.median(solved) if solved else 0
            print(f"  {name:<12}  sat={c['sat']:>6}  unsat={c['unsat']:>5}  "
                  f"TO={c.get('timeout',0):>4}  err={c.get('error',0):>4}  "
                  f"crash={c.get('crash',0):>3}  "
                  f"avg={avg:.3f}s  median={med:.4f}s")
        print()


def summarize(label, paired, solver_a, solver_b):
    """Print a full summary for a set of paired results."""
    print(f"{BOLD}{'═' * 78}{RST}")
    print(f"{BOLD}  {label}{RST}")
    print(f"{'═' * 78}")
    print(f"  Files: {len(paired):,}")
    print()

    # Per-solver summary
    for name in [solver_a, solver_b]:
        vals = [v[0] if name == solver_a else v[1] for v in paired.values()]
        c = Counter(v["answer"] for v in vals)
        solved = [v["elapsed"] for v in vals if v["answer"] in ("sat", "unsat")]
        avg = statistics.mean(solved) if solved else 0
        med = statistics.median(solved) if solved else 0
        print(f"  {name:<12}  sat={c['sat']:>6}  unsat={c['unsat']:>5}  "
              f"TO={c.get('timeout',0):>4}  err={c.get('error',0):>4}  "
              f"crash={c.get('crash',0):>3}  "
              f"avg={avg:.3f}s  median={med:.4f}s")
    print()

    # Scoreboard
    WIN = 0.5
    LOSE = 5.0
    MASSIVE = 20.0

    wins = losses = massive = ties = 0
    unique_solves = unique_fails = 0
    disagree = []
    new_timeouts = []
    recovered_timeouts = []
    slowdowns = []
    speedups = []

    for path, (a, b) in paired.items():
        a_ans = a["answer"]
        b_ans = b["answer"]
        a_ok = a_ans in ("sat", "unsat")
        b_ok = b_ans in ("sat", "unsat")
        a_t = a["elapsed"]
        b_t = b["elapsed"]

        # Answer disagreements
        if a_ok and b_ok and a_ans != b_ans:
            disagree.append((a_ans, b_ans, path))

        # Unique solves / fails (from solver_b's perspective)
        if b_ok and not a_ok and a_ans == "timeout":
            recovered_timeouts.append((b_t, b_ans, path))
            unique_solves += 1
            continue
        if not b_ok and b_ans == "timeout" and a_ok:
            new_timeouts.append((a_t, a_ans, path))
            unique_fails += 1
            continue

        if not a_ok or not b_ok:
            continue

        if a_t < 0.001 and b_t < 0.001:
            ties += 1
            continue

        denom = max(a_t, 0.001)
        ratio = b_t / denom

        if ratio >= MASSIVE:
            massive += 1
            losses += 1
            slowdowns.append((ratio, a_t, b_t, path))
        elif ratio >= LOSE:
            losses += 1
            slowdowns.append((ratio, a_t, b_t, path))
        elif ratio >= 3.0:
            slowdowns.append((ratio, a_t, b_t, path))
            ties += 1
        elif ratio <= 1.0 / 3.0:
            speedups.append((ratio, a_t, b_t, path))
            wins += 1
        elif ratio <= WIN:
            wins += 1
            speedups.append((ratio, a_t, b_t, path))
        else:
            ties += 1

    print(f"  {BOLD}{solver_b} Scoreboard{RST}")
    print(f"  {'─' * 74}")
    print(f"  {GREEN}Wins (2x+ faster):{RST}  {wins:>6,}   "
          f"{YELLOW}Ties:{RST} {ties:>6,}   "
          f"{RED}Losses (5x+ slower):{RST} {losses:>6,}")
    print(f"  {GREEN}Unique solves:{RST}      {unique_solves:>6,}   "
          f"{MAGENTA}Massive (20x+):{RST} {massive:>5,}   "
          f"{RED}Unique timeouts:{RST}    {unique_fails:>6,}")
    print()

    # Disagreements
    print(f"  Answer disagreements: {len(disagree)}")
    for a_ans, b_ans, p in disagree:
        print(f"    {solver_a}={a_ans}  {solver_b}={b_ans}  {shorten(p)}")
    print()

    # New timeouts
    if new_timeouts:
        new_timeouts.sort()
        print(f"  {RED}New timeouts ({len(new_timeouts)} files {solver_a} solved, {solver_b} timed out):{RST}")
        for t, ans, p in new_timeouts[:15]:
            print(f"    {solver_a}={t:.3f}s [{ans}]  {shorten(p)}")
        if len(new_timeouts) > 15:
            print(f"    ... and {len(new_timeouts) - 15} more")
        print()

    # Recovered timeouts
    if recovered_timeouts:
        recovered_timeouts.sort()
        print(f"  {GREEN}Recovered timeouts ({len(recovered_timeouts)} files {solver_a} timed out, {solver_b} solved):{RST}")
        for t, ans, p in recovered_timeouts[:15]:
            print(f"    {solver_b}={t:.3f}s [{ans}]  {shorten(p)}")
        if len(recovered_timeouts) > 15:
            print(f"    ... and {len(recovered_timeouts) - 15} more")
        print()

    # Top slowdowns
    if slowdowns:
        slowdowns.sort(key=lambda x: -x[0])
        print(f"  Top slowdowns ({solver_b} vs {solver_a}):")
        for ratio, at, bt, p in slowdowns[:10]:
            print(f"    {ratio:>6.1f}x  {solver_a}={at:.3f}s  {solver_b}={bt:.3f}s  {shorten(p)}")
        print()

    # Top speedups
    if speedups:
        speedups.sort(key=lambda x: x[0])
        print(f"  Top speedups ({solver_b} vs {solver_a}):")
        for ratio, at, bt, p in speedups[:10]:
            inv = 1.0 / ratio if ratio > 0 else 999
            print(f"    {inv:>6.1f}x  {solver_a}={at:.3f}s  {solver_b}={bt:.3f}s  {shorten(p)}")
        print()


def print_score_tables(paired, solver_a, solver_b):
    """Print competition-style score tables at 24s and 2m virtual timeouts."""
    for vto in [24, 120]:
        score_table(paired, solver_a, solver_b, vto)


def main():
    parser = argparse.ArgumentParser(description="Summarize a build comparison run")
    parser.add_argument("main_csv", help="Main pass CSV")
    parser.add_argument("reval_csv", nargs="?", default=None,
                        help="Revalidation CSV (optional)")
    parser.add_argument("--solver-a", default="master", help="First solver (default: master)")
    parser.add_argument("--solver-b", default="incremental", help="Second solver (default: incremental)")
    parser.add_argument("--by-theory", action="store_true", help="Show per-solver summary grouped by theory")
    args = parser.parse_args()

    main_data = load_medians(args.main_csv)

    # Summary 1: main pass only
    main_paired = pair_files(main_data, args.solver_a, args.solver_b)
    summarize("Main pass only", main_paired, args.solver_a, args.solver_b)

    if args.reval_csv and os.path.exists(args.reval_csv):
        # Summary 2: combined (revalidation replaces main)
        combined = load_combined(args.main_csv, args.reval_csv)
        combined_paired = pair_files(combined, args.solver_a, args.solver_b)
        summarize("Combined (revalidation replaces main pass)", combined_paired, args.solver_a, args.solver_b)

        if args.by_theory:
            summarize_by_theory(combined_paired, args.solver_a, args.solver_b)

        print_score_tables(combined_paired, args.solver_a, args.solver_b)
    else:
        print_score_tables(main_paired, args.solver_a, args.solver_b)


if __name__ == "__main__":
    main()
