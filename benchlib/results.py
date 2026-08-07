"""CSV loading, result data structures, and file pairing."""

import csv
import statistics
from dataclasses import dataclass
from pathlib import Path
from threading import Lock


@dataclass
class Result:
    path: str
    solver: str
    run: int
    elapsed: float
    answer: str       # "sat", "unsat", "timeout", "error", "crash", or raw first line
    exit_code: int
    signal_num: int
    timeout_used: float


def load_medians(csv_path):
    """Load a CSV and compute per-(file, solver) median elapsed and majority answer.

    Returns dict keyed by (path, solver) with values
    {"answer": str, "elapsed": float, "timeout": float}.
    """
    raw = {}
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            key = (row["path"], row["solver"])
            raw.setdefault(key, []).append({
                "elapsed": float(row["elapsed"]),
                "answer": row["answer"],
                "timeout": float(row["timeout"]),
            })

    results = {}
    for (path, solver), runs in raw.items():
        answers = [r["answer"] for r in runs]
        ans = max(set(answers), key=answers.count)
        med = statistics.median(r["elapsed"] for r in runs)
        to = runs[0]["timeout"]
        results[(path, solver)] = {"answer": ans, "elapsed": med, "timeout": to}
    return results


def load_combined(main_csv, reval_csv):
    """Load main + revalidation CSVs, with reval overriding main for same keys.

    Returns dict in the same format as load_medians.
    """
    combined = load_medians(main_csv)
    reval = load_medians(reval_csv)
    combined.update(reval)
    return combined


def pair_files(data, solver_a, solver_b):
    """Group results by file, returning only files with both solvers.

    Returns dict keyed by path with values (solver_a_result, solver_b_result).
    """
    files_a = {p: v for (p, s), v in data.items() if s == solver_a}
    files_b = {p: v for (p, s), v in data.items() if s == solver_b}
    common = set(files_a.keys()) & set(files_b.keys())
    return {p: (files_a[p], files_b[p]) for p in common}


class ResultLog:
    """Thread-safe incremental CSV writer for benchmark results."""

    HEADER = ["path", "solver", "run", "elapsed", "answer", "exit_code", "signal", "timeout"]

    def __init__(self, path):
        self.path = path
        self.lock = Lock()
        with open(self.path, "w", newline="") as f:
            csv.writer(f).writerow(self.HEADER)

    def record(self, r):
        with self.lock:
            with open(self.path, "a", newline="") as f:
                csv.writer(f).writerow([
                    r.path, r.solver, r.run, f"{r.elapsed:.3f}",
                    r.answer, r.exit_code, r.signal_num, f"{r.timeout_used:.0f}"
                ])


def score_table(paired, solver_a, solver_b, virtual_timeout):
    """Print a competition-style score table at a given virtual timeout.

    Applies a virtual timeout: any file with elapsed >= vto is treated as
    unsolved. Computes solved/unsolved/mean/median/PAR-2 for both solvers.
    """
    from .fmt import BOLD, RST

    a_times = []
    b_times = []
    a_solved = b_solved = 0
    total = len(paired)

    for path, (a, b) in paired.items():
        for data, times_list in [(a, a_times), (b, b_times)]:
            ans = data["answer"]
            t = data["elapsed"]
            if ans in ("sat", "unsat") and t < virtual_timeout:
                times_list.append(t)
            else:
                times_list.append(2 * virtual_timeout)  # PAR-2 penalty

    a_solved = sum(1 for t in a_times if t < virtual_timeout)
    b_solved = sum(1 for t in b_times if t < virtual_timeout)
    a_unsolved = total - a_solved
    b_unsolved = total - b_solved
    a_mean = statistics.mean(a_times) if a_times else 0
    b_mean = statistics.mean(b_times) if b_times else 0
    a_median = statistics.median(a_times) if a_times else 0
    b_median = statistics.median(b_times) if b_times else 0
    a_par2 = sum(a_times)
    b_par2 = sum(b_times)

    # Compute deltas
    d_solved = b_solved - a_solved
    d_unsolved = b_unsolved - a_unsolved
    d_mean = b_mean - a_mean
    d_median = b_median - a_median
    d_par2 = b_par2 - a_par2

    # Format delta strings
    def fmt_delta_int(d):
        return f"+{d}" if d >= 0 else str(d)

    def fmt_delta_time(d, decimals=3):
        sign = "-" if d < 0 else "+"
        return f"{sign}{abs(d):.{decimals}f}s"

    median_ratio = ""
    if a_median > 0 and b_median > 0 and a_median != b_median:
        if b_median < a_median:
            median_ratio = f" ({a_median/b_median:.2f}x faster)"
        else:
            median_ratio = f" ({b_median/a_median:.2f}x slower)"

    par2_pct = ""
    if a_par2 > 0 and d_par2 != 0:
        pct = abs(d_par2) / a_par2 * 100
        direction = "lower" if d_par2 < 0 else "higher"
        par2_pct = f" ({pct:.1f}% {direction})"

    vto_label = f"{virtual_timeout}s" if virtual_timeout < 60 else f"{virtual_timeout/60:.0f}m"
    print(f"{BOLD}  Competition Score (virtual timeout = {vto_label}){RST}")
    print(f"  {'─' * 74}")
    print(f"  {'':10} {solver_a:>12} {solver_b:>12}   {'Delta':>24}")
    print(f"  {'─' * 74}")
    print(f"  {'Solved':10} {a_solved:>12,} {b_solved:>12,}   {fmt_delta_int(d_solved):>24}")
    print(f"  {'Unsolved':10} {a_unsolved:>12,} {b_unsolved:>12,}   {fmt_delta_int(d_unsolved):>24}")
    print(f"  {'Mean':10} {a_mean:>11.3f}s {b_mean:>11.3f}s   {fmt_delta_time(d_mean):>24}")
    print(f"  {'Median':10} {a_median:>11.3f}s {b_median:>11.3f}s   {fmt_delta_time(d_median) + median_ratio:>24}")
    print(f"  {'PAR-2':10} {a_par2:>11,.0f}s {b_par2:>11,.0f}s   {fmt_delta_time(d_par2, 0) + par2_pct:>24}")
    print()
