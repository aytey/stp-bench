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
