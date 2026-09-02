"""CSV loading, result data structures, and file pairing."""

import csv
import json
import os
import statistics
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

# Only sat/unsat count as a solver having done the work. `unknown` is
# deliberately excluded: a solver that bails out on a query it does not
# support answers `unknown` in milliseconds, and timing that against a solver
# that actually solves the query measures nothing but the difference between
# attempting and not attempting.
CONCLUSIVE_ANSWERS = ("sat", "unsat")


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


def majority_answer(answers):
    """The most common answer across a set of runs."""
    return max(set(answers), key=answers.count)


def solvers_in_csv(csv_path):
    """Distinct solver names in a results CSV, in first-appearance order.

    That is completion order, not the order the config listed them in, so
    prefer `solvers_for_csv` when the baseline needs to come out first.
    """
    seen = []
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            if row["solver"] not in seen:
                seen.append(row["solver"])
    return seen


def manifest_path(csv_path):
    """Path of the run manifest belonging to a results CSV."""
    p = Path(csv_path)
    stem = p.stem
    for suffix in ("_revalidation", "_answer_disagreements"):
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    return p.parent / f"{stem}_manifest.json"


def load_manifest(csv_path):
    """The run manifest for a results CSV, or None if it was not written."""
    try:
        with open(manifest_path(csv_path)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def write_manifest(csv_path, manifest):
    """Record what produced a results CSV, next to it."""
    path = manifest_path(csv_path)
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    return path


def solvers_for_csv(csv_path):
    """Solver names for a results CSV, baseline first.

    Rows land in completion order, which says nothing about which solver was
    the baseline, so use the manifest the run wrote and fall back to CSV
    order only when there is none.
    """
    present = solvers_in_csv(csv_path)
    manifest = load_manifest(csv_path)
    if not manifest:
        return present
    ordered = [s["name"] for s in manifest.get("solvers", []) if s["name"] in present]
    return ordered + [n for n in present if n not in ordered]


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
        ans = majority_answer([r["answer"] for r in runs])
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


def collect_answer_disagreements(data, solver_names):
    """Files where two solvers returned conflicting conclusive answers.

    A sat/unsat split is a soundness bug in one of the solvers, so it is
    reported separately from any timing difference. Returns a list of
    (path, {solver: answer}) sorted by file size, smallest first, so the
    easiest reproducer comes out on top.
    """
    by_path = {}
    for (path, solver), values in data.items():
        by_path.setdefault(path, {})[solver] = values["answer"]

    disagreements = []
    for path, answers in by_path.items():
        conclusive = {a for a in answers.values() if a in CONCLUSIVE_ANSWERS}
        if len(conclusive) > 1:
            disagreements.append((path, {s: answers.get(s, "-") for s in solver_names}))

    def sort_key(item):
        path = item[0]
        try:
            return (0, os.path.getsize(path), path)
        except OSError:
            return (1, 0, path)

    return sorted(disagreements, key=sort_key)


def write_answer_disagreements(path, disagreements, solver_names):
    """Write the full disagreement list to CSV, one row per file."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["path", *solver_names])
        for input_path, answers in disagreements:
            writer.writerow([input_path, *(answers[s] for s in solver_names)])


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
            if ans in CONCLUSIVE_ANSWERS and t < virtual_timeout:
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

    vto_label = f"{virtual_timeout:g}s" if virtual_timeout < 60 else f"{virtual_timeout/60:.0f}m"
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
