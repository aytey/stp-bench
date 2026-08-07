#!/usr/bin/env python3
"""
Compare two build_comparison CSV runs for a given solver.

Usage:
    python3 compare_runs.py build_comparison_old.csv build_comparison.csv [--solver incremental]
"""

import argparse
import csv
import statistics
import sys


def load(path, solver):
    per_file = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            if row["solver"] != solver:
                continue
            p = row["path"]
            per_file.setdefault(p, []).append({
                "elapsed": float(row["elapsed"]),
                "answer": row["answer"],
            })
    results = {}
    for p, runs in per_file.items():
        answers = [r["answer"] for r in runs]
        ans = max(set(answers), key=answers.count)
        med = statistics.median(r["elapsed"] for r in runs)
        results[p] = {"answer": ans, "elapsed": med}
    return results


def shorten(p):
    for tag in ("non-incremental/", "incremental/"):
        if tag in p:
            return p.split(tag, 1)[-1]
    return p.split("/")[-1]


def main():
    parser = argparse.ArgumentParser(description="Compare two CSV runs for a solver")
    parser.add_argument("old", help="Old CSV file")
    parser.add_argument("new", help="New CSV file")
    parser.add_argument("--solver", default="incremental", help="Solver name to compare (default: incremental)")
    args = parser.parse_args()

    old = load(args.old, args.solver)
    new = load(args.new, args.solver)
    common = set(old.keys()) & set(new.keys())

    print(f"Old: {len(old):,} files    New: {len(new):,} files    Common: {len(common):,} files")
    print()

    # Per-run summary (common files only)
    from collections import Counter
    for label, data in [("Old", old), ("New", new)]:
        common_data = {k: v for k, v in data.items() if k in common}
        c = Counter(v["answer"] for v in common_data.values())
        times = [v["elapsed"] for v in common_data.values() if v["answer"] in ("sat", "unsat")]
        avg = statistics.mean(times) if times else 0
        med = statistics.median(times) if times else 0
        print(f"  {label}:  sat={c['sat']:>6}  unsat={c['unsat']:>5}  "
              f"TO={c['timeout']:>4}  err={c['error']:>4}  crash={c['crash']:>3}  "
              f"avg={avg:.3f}s  median={med:.3f}s")

    print()

    # Timing comparison on common solved files
    faster = slower = same = 0
    big_faster = big_slower = 0
    outliers = []
    for p in common:
        o = old[p]
        n = new[p]
        if o["answer"] not in ("sat", "unsat") or n["answer"] not in ("sat", "unsat"):
            continue

        ot = o["elapsed"]
        nt = n["elapsed"]

        if ot < 0.001 and nt < 0.001:
            same += 1
            continue

        denom = max(ot, 0.001)
        ratio = nt / denom

        if ratio <= 0.5:
            faster += 1
            if ratio <= 0.2:
                big_faster += 1
            outliers.append(("FASTER", ratio, ot, nt, p))
        elif ratio >= 2.0:
            slower += 1
            if ratio >= 5.0:
                big_slower += 1
            outliers.append(("SLOWER", ratio, ot, nt, p))
        else:
            same += 1

    print(f"Timing comparison (common solved files):")
    print(f"  New 2x+ faster: {faster:>5}    (of which 5x+: {big_faster})")
    print(f"  Similar:         {same:>5}")
    print(f"  New 2x+ slower:  {slower:>5}    (of which 5x+: {big_slower})")
    print()

    # Answer disagreements
    disagree = []
    for p in common:
        o_ans = old[p]["answer"]
        n_ans = new[p]["answer"]
        if o_ans in ("sat", "unsat") and n_ans in ("sat", "unsat") and o_ans != n_ans:
            disagree.append((o_ans, n_ans, p))

    print(f"Answer disagreements: {len(disagree)}")
    for o_ans, n_ans, p in disagree:
        print(f"  old={o_ans}  new={n_ans}  {shorten(p)}")

    # New timeouts (solved in old, timed out in new)
    new_timeouts = []
    for p in common:
        o_ans = old[p]["answer"]
        n_ans = new[p]["answer"]
        if o_ans in ("sat", "unsat") and n_ans == "timeout":
            new_timeouts.append((old[p]["elapsed"], o_ans, p))

    if new_timeouts:
        new_timeouts.sort()
        print()
        print(f"New timeouts ({len(new_timeouts)} files solved in old, timed out in new):")
        for ot, o_ans, p in new_timeouts:
            print(f"  old={ot:.3f}s [{o_ans}]  {shorten(p)}")

    # Recovered timeouts (timed out in old, solved in new)
    recovered = []
    for p in common:
        o_ans = old[p]["answer"]
        n_ans = new[p]["answer"]
        if o_ans == "timeout" and n_ans in ("sat", "unsat"):
            recovered.append((new[p]["elapsed"], n_ans, p))

    if recovered:
        recovered.sort()
        print()
        print(f"Recovered timeouts ({len(recovered)} files timed out in old, solved in new):")
        for nt, n_ans, p in recovered:
            print(f"  new={nt:.3f}s [{n_ans}]  {shorten(p)}")

    # Outliers
    if outliers:
        print()
        top_faster = sorted([x for x in outliers if x[0] == "FASTER"], key=lambda x: x[1])[:10]
        top_slower = sorted([x for x in outliers if x[0] == "SLOWER"], key=lambda x: -x[1])[:10]

        if top_slower:
            print(f"Top slowdowns (new vs old):")
            for tag, ratio, ot, nt, p in top_slower:
                print(f"  {ratio:>6.1f}x  old={ot:.3f}s  new={nt:.3f}s  {shorten(p)}")

        if top_faster:
            print()
            print(f"Top speedups (new vs old):")
            for tag, ratio, ot, nt, p in top_faster:
                inv = 1.0 / ratio if ratio > 0 else 999
                print(f"  {inv:>6.1f}x  old={ot:.3f}s  new={nt:.3f}s  {shorten(p)}")


if __name__ == "__main__":
    main()
