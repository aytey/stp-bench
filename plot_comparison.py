#!/usr/bin/env python3
"""
Generate per-logic gnuplot scatter plots comparing master vs incremental.

Usage:
    python3 plot_comparison.py build_comparison.csv build_comparison_revalidation.csv
"""

import argparse
import csv
import os
import statistics
import subprocess
import sys


def load_combined(main_csv, reval_csv):
    """Load main + revalidation CSVs, reval replaces main for same (path, solver)."""
    raw = {}
    for path in [main_csv, reval_csv]:
        with open(path) as f:
            for row in csv.DictReader(f):
                key = (row["path"], row["solver"])
                raw.setdefault(key, []).append({
                    "elapsed": float(row["elapsed"]),
                    "answer": row["answer"],
                    "timeout": float(row["timeout"]),
                })
    # For reval, it overwrites main, so just load main first then reval
    raw_main = {}
    with open(main_csv) as f:
        for row in csv.DictReader(f):
            key = (row["path"], row["solver"])
            raw_main.setdefault(key, []).append({
                "elapsed": float(row["elapsed"]),
                "answer": row["answer"],
            })

    raw_reval = {}
    with open(reval_csv) as f:
        for row in csv.DictReader(f):
            key = (row["path"], row["solver"])
            raw_reval.setdefault(key, []).append({
                "elapsed": float(row["elapsed"]),
                "answer": row["answer"],
            })

    combined = {}
    all_keys = set(raw_main.keys()) | set(raw_reval.keys())
    for key in all_keys:
        runs = raw_reval.get(key, raw_main.get(key, []))
        answers = [r["answer"] for r in runs]
        ans = max(set(answers), key=answers.count)
        med = statistics.median(r["elapsed"] for r in runs)
        combined[key] = {"answer": ans, "elapsed": med}

    return combined


def extract_logic(path):
    for tag in ("non-incremental/", "incremental/"):
        if tag in path:
            rest = path.split(tag, 1)[-1]
            return rest.split("/")[0]
    return "unknown"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("main_csv")
    parser.add_argument("reval_csv")
    parser.add_argument("--outdir", default=".")
    args = parser.parse_args()

    data = load_combined(args.main_csv, args.reval_csv)

    # Pair files
    files_m = {p: v for (p, s), v in data.items() if s == "master"}
    files_i = {p: v for (p, s), v in data.items() if s == "incremental"}
    common = set(files_m.keys()) & set(files_i.keys())

    # Group by logic
    by_logic = {}
    for p in common:
        m = files_m[p]
        i = files_i[p]
        # Only plot files where both got an answer (sat/unsat)
        if m["answer"] not in ("sat", "unsat") or i["answer"] not in ("sat", "unsat"):
            continue
        logic = extract_logic(p)
        by_logic.setdefault(logic, []).append((m["elapsed"], i["elapsed"]))

    for logic in sorted(by_logic.keys()):
        points = by_logic[logic]
        dat_path = os.path.join(args.outdir, f"scatter_{logic}.dat")
        svg_path = os.path.join(args.outdir, f"scatter_{logic}.svg")
        gp_path = os.path.join(args.outdir, f"scatter_{logic}.gp")

        with open(dat_path, "w") as f:
            f.write("# master_time incremental_time\n")
            for mt, it in points:
                f.write(f"{mt:.6f} {it:.6f}\n")

        # Find axis range
        all_times = [t for pair in points for t in pair]
        lo = max(min(all_times) * 0.5, 0.0001)
        hi = max(all_times) * 2

        wins = sum(1 for mt, it in points if it < mt * 0.5)
        losses = sum(1 for mt, it in points if it > mt * 5.0)
        total = len(points)

        gp_script = f"""\
set terminal svg size 800,800 font "Arial,12"
set output "{svg_path}"

set title "{logic} — master vs incremental ({total:,} files, {wins} wins, {losses} losses)"
set xlabel "master (seconds)"
set ylabel "incremental (seconds)"

set logscale x
set logscale y
set format x "%.3f"
set format y "%.3f"

set xrange [{lo}:{hi}]
set yrange [{lo}:{hi}]

set size square

# Diagonal: equal performance
set style line 1 lt 1 lc rgb "#888888" lw 1 dt 2

# 2x lines
set style line 2 lt 1 lc rgb "#aaaaaa" lw 0.5 dt 3

# Points
set style line 10 lt 1 lc rgb "#2266cc" pt 7 ps 0.4

plot x with lines ls 1 title "equal", \\
     2*x with lines ls 2 title "2x slower", \\
     0.5*x with lines ls 2 title "2x faster", \\
     "{dat_path}" using 1:2 with points ls 10 title "{logic}"
"""
        with open(gp_path, "w") as f:
            f.write(gp_script)

        subprocess.run(["gnuplot", gp_path], check=True)
        print(f"  {svg_path}  ({total:,} points)")

    print(f"\nDone — {len(by_logic)} plots generated.")


if __name__ == "__main__":
    main()
