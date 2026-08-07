#!/usr/bin/env python3
"""
Analyze solver_comparison.csv and render the same tables/scoreboard
that the TUI showed live, plus extras like disagreements and top slowdowns.
"""

import argparse
import csv
import sys
from collections import defaultdict

BOLD = "\033[1m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
DIM = "\033[2m"
RST = "\033[0m"

WIN_RATIO = 0.5
LOSE_RATIO = 5.0
MASSIVE_RATIO = 20.0


def load(path):
    by_file = defaultdict(dict)
    with open(path) as f:
        for r in csv.DictReader(f):
            by_file[r["path"]][r["solver"]] = {
                "elapsed": float(r["elapsed"]),
                "answer": r["answer"],
                "exit_code": int(r["exit_code"]),
                "signal": int(r["signal"]),
            }
    return by_file


def shorten(path):
    for tag in ("non-incremental/", "incremental/"):
        if tag in path:
            return path.split(tag, 1)[-1]
    return path


def main():
    parser = argparse.ArgumentParser(description="Analyze solver comparison CSV")
    parser.add_argument("csv", nargs="?", default="solver_comparison.csv")
    parser.add_argument("--top", type=int, default=20,
                        help="Number of top wins/losses to show")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()

    if args.no_color:
        for name in ("BOLD", "RED", "GREEN", "YELLOW", "CYAN", "MAGENTA", "DIM", "RST"):
            globals()[name] = ""

    by_file = load(args.csv)
    solvers = set()
    for results in by_file.values():
        solvers.update(results.keys())
    solver_names = sorted(solvers)

    # ── Per-solver stats ──────────────────────────────────────────────────
    stats = {s: {"done": 0, "sat": 0, "unsat": 0, "unknown": 0,
                 "timeout": 0, "error": 0, "crash": 0, "other": 0,
                 "total_time": 0.0}
             for s in solver_names}

    for results in by_file.values():
        for solver, r in results.items():
            s = stats[solver]
            s["done"] += 1
            s["total_time"] += r["elapsed"]
            a = r["answer"]
            if a in ("sat", "unsat", "unknown", "timeout", "error", "crash"):
                s[a] += 1
            else:
                s["other"] += 1

    print()
    print(f"{BOLD}{'═' * 78}{RST}")
    print(f"{BOLD}  Solver Comparison Results{RST}  ({len(by_file):,} files)")
    print(f"{'═' * 78}")
    print()

    hdr = f"  {'Solver':<12} {'Done':>7} {'sat':>7} {'unsat':>7} {'TO':>6} {'err':>5} {'crash':>5} {'avg(s)':>7}"
    print(f"{BOLD}{hdr}{RST}")
    print(f"  {'─' * 74}")
    for name in solver_names:
        s = stats[name]
        avg = s["total_time"] / s["done"] if s["done"] else 0
        print(f"  {name:<12} {s['done']:>7,} {s['sat']:>7,} {s['unsat']:>7,} "
              f"{s['timeout']:>6,} {s['error']:>5,} {s['crash']:>5,} {avg:>7.2f}")

    # ── Disagreements ─────────────────────────────────────────────────────
    disagreements = []
    for path, results in by_file.items():
        real = {k: v["answer"] for k, v in results.items() if v["answer"] in ("sat", "unsat")}
        if len(set(real.values())) > 1:
            disagreements.append((path, real))

    print()
    print(f"{BOLD}  Disagreements{RST}  (solvers gave different sat/unsat answers)")
    print(f"  {'─' * 74}")
    if disagreements:
        for path, answers in disagreements[:50]:
            parts = "  ".join(f"{k}={v}" for k, v in sorted(answers.items()))
            print(f"  {RED}{parts}{RST}  {shorten(path)}")
        if len(disagreements) > 50:
            print(f"  {DIM}... and {len(disagreements) - 50} more{RST}")
        print(f"\n  {RED}{BOLD}Total disagreements: {len(disagreements)}{RST}")
    else:
        print(f"  {GREEN}None — all solvers agree on sat/unsat.{RST}")

    # ── STP scoreboard ────────────────────────────────────────────────────
    if "stp" not in solvers:
        print(f"\n  {YELLOW}No 'stp' solver in data, skipping scoreboard.{RST}")
        return

    wins = []      # (ratio, path, stp_time, best_name, best_time)  ratio < 1 = STP faster
    losses = []    # (ratio, path, stp_time, best_name, best_time)  ratio > 1 = STP slower
    ties = 0
    unique_solves = []
    unique_timeouts = []
    massive = 0

    for path, results in by_file.items():
        stp = results.get("stp")
        if not stp:
            continue
        others = {k: v for k, v in results.items() if k != "stp"}
        if not others:
            continue

        stp_ok = stp["answer"] in ("sat", "unsat", "unknown")
        others_ok = {k: v for k, v in others.items() if v["answer"] in ("sat", "unsat", "unknown")}
        others_to = {k: v for k, v in others.items() if v["answer"] == "timeout"}

        if stp_ok and not others_ok and others_to:
            unique_solves.append((stp["elapsed"], path))
            continue
        if not stp_ok and stp["answer"] == "timeout" and others_ok:
            best = min(others_ok.items(), key=lambda kv: kv[1]["elapsed"])
            unique_timeouts.append((best[1]["elapsed"], best[0], path))
            continue
        if not stp_ok or not others_ok:
            continue

        stp_t = stp["elapsed"]
        best_name, best_v = min(others_ok.items(), key=lambda kv: kv[1]["elapsed"])
        best_t = best_v["elapsed"]

        if best_t < 0.001 and stp_t < 0.001:
            ties += 1
            continue

        denom = max(best_t, 0.001)
        ratio = stp_t / denom

        if ratio >= LOSE_RATIO:
            losses.append((ratio, path, stp_t, best_name, best_t))
            if ratio >= MASSIVE_RATIO:
                massive += 1
        elif ratio <= WIN_RATIO:
            wins.append((ratio, path, stp_t, best_name, best_t))
        else:
            ties += 1

    wins.sort(key=lambda x: x[0])        # best wins first (lowest ratio)
    losses.sort(key=lambda x: -x[0])     # worst losses first (highest ratio)

    print()
    print(f"{BOLD}{'═' * 78}{RST}")
    print(f"{BOLD}  STP Scoreboard{RST}")
    print(f"{'═' * 78}")
    print()
    print(f"  {GREEN}Wins (2x+ faster):{RST}  {len(wins):>6,}   "
          f"{YELLOW}Ties:{RST} {ties:>6,}   "
          f"{RED}Losses (5x+ slower):{RST} {len(losses):>6,}")
    print(f"  {GREEN}Unique solves:{RST}      {len(unique_solves):>6,}   "
          f"{MAGENTA}Massive (20x+):{RST} {massive:>5,}   "
          f"{RED}Unique timeouts:{RST}    {len(unique_timeouts):>6,}")

    # Top wins
    print()
    print(f"{BOLD}  Top {args.top} STP wins (fastest relative to best other solver):{RST}")
    print(f"  {'─' * 74}")
    if wins:
        for ratio, path, stp_t, best_name, best_t in wins[:args.top]:
            inv = 1.0 / ratio if ratio > 0 else 999
            print(f"  {GREEN}{stp_t:>8.2f}s vs {best_name} {best_t:.2f}s "
                  f"({inv:>5.1f}x faster){RST}  {shorten(path)}")
    else:
        print(f"  {DIM}(none){RST}")

    # Top losses
    print()
    print(f"{BOLD}  Top {args.top} STP losses (slowest relative to best other solver):{RST}")
    print(f"  {'─' * 74}")
    if losses:
        for ratio, path, stp_t, best_name, best_t in losses[:args.top]:
            print(f"  {RED}{stp_t:>8.2f}s vs {best_name} {best_t:.2f}s "
                  f"({ratio:>7.1f}x slower){RST}  {shorten(path)}")
    else:
        print(f"  {DIM}(none){RST}")

    # Unique timeouts
    if unique_timeouts:
        unique_timeouts.sort(key=lambda x: x[0])  # fastest other first
        print()
        print(f"{BOLD}  STP timed out but others solved ({len(unique_timeouts):,} files):{RST}")
        print(f"  {'─' * 74}")
        for best_t, best_name, path in unique_timeouts[:args.top]:
            print(f"  {RED}STP TIMEOUT  ({best_name} did it in {best_t:.2f}s){RST}  {shorten(path)}")
        if len(unique_timeouts) > args.top:
            print(f"  {DIM}... and {len(unique_timeouts) - args.top} more{RST}")

    # Unique solves
    if unique_solves:
        unique_solves.sort(key=lambda x: x[0])
        print()
        print(f"{BOLD}  STP solved but others timed out ({len(unique_solves):,} files):{RST}")
        print(f"  {'─' * 74}")
        for stp_t, path in unique_solves[:args.top]:
            print(f"  {GREEN}STP {stp_t:.2f}s (others timed out){RST}  {shorten(path)}")
        if len(unique_solves) > args.top:
            print(f"  {DIM}... and {len(unique_solves) - args.top} more{RST}")

    print()
    print(f"{'═' * 78}")


if __name__ == "__main__":
    main()
