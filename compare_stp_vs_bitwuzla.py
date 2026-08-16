#!/usr/bin/env python3
"""
Compare STP (uf branch) vs Bitwuzla on UF benchmarks.
Runs .smt2 files against both solvers multiple times, records timing,
and uses median times for comparison.

Output: a CSV with one row per (file, solver, run) triple.
"""

import argparse
import csv
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

from benchlib import (
    BOLD, DIM, RED, GREEN, YELLOW, CYAN, MAGENTA, RST,
    CL, HIDE, SHOW,
    Result, ResultLog, fmt_duration, score_table,
    collect_smt2_files, load_file_list, load_medians, pair_files, shorten,
)
from benchlib.runner import run_one, run_pool

# ── Defaults ──────────────────────────────────────────────────────────────────

SOLVERS = {
    "bitwuzla":  (Path("/home/avj/clones/bitwuzla/build/src/main/bitwuzla"), []),
    "stp":       (Path("/home/avj/clones/stp/uf/build/stp"), ["--uninterpreted-functions", "--array-equality"]),
}

DEFAULT_DIR = Path("/mnt/baranem/uf_bench")

DEFAULT_TIMEOUT = 30.0  # 30 seconds
DEFAULT_WALL_HOURS = 24.0
DEFAULT_WORKERS = os.cpu_count() or 1
DEFAULT_RUNS = 3


# ── TUI ───────────────────────────────────────────────────────────────────────

class TUI:
    # Thresholds for classification
    WIN_RATIO = 0.5     # alt is 2x+ faster -> win
    LOSE_RATIO = 5.0    # alt is 5x+ slower -> loss
    MASSIVE_RATIO = 20.0  # alt is 20x+ slower -> massive loss

    def __init__(self, total_tasks: int, total_files: int,
                 solver_names: list[str], runs: int):
        self.total_tasks = total_tasks
        self.total_files = total_files
        self.solver_names = solver_names
        self.runs = runs
        # The "base" solver is the first, the "alt" is the second
        self._base_name = solver_names[0]
        self._alt_name = solver_names[1] if len(solver_names) > 1 else solver_names[0]
        self.completed = 0
        self.per_solver = {s: {"done": 0, "sat": 0, "unsat": 0, "unknown": 0,
                               "timeout": 0, "error": 0, "crash": 0, "other": 0,
                               "total_time": 0.0, "solved_time": 0.0, "solved": 0}
                           for s in solver_names}
        self.start_time = time.monotonic()
        from threading import Lock
        self.lock = Lock()
        self._lines = 0

        # Per-file result accumulation: path -> solver -> list[Result]
        self._file_results: dict[str, dict[str, list[Result]]] = {}
        self._expected_per_file = len(solver_names) * runs
        self._file_task_count: dict[str, int] = {}
        self._files_compared = 0
        self._alt_wins = 0
        self._alt_losses = 0
        self._alt_massive = 0
        self._ties = 0
        self._alt_unique_solves = 0
        self._alt_unique_fails = 0

        # Recent highlights
        self._recent_wins: list[str] = []
        self._recent_losses: list[str] = []
        self._max_recent = 5

    def start(self):
        sys.stderr.write(HIDE)
        sys.stderr.flush()

    def stop(self):
        sys.stderr.write(SHOW)
        sys.stderr.flush()

    def _classify_file(self, file_results: dict[str, list[Result]]):
        """Once all runs for both builds are done, classify using medians."""
        self._files_compared += 1

        alt_runs = file_results.get(self._alt_name, [])
        base_runs = file_results.get(self._base_name, [])
        if not alt_runs or not base_runs:
            return

        # Use majority answer (most common across runs)
        def majority_answer(runs):
            answers = [r.answer for r in runs]
            return max(set(answers), key=answers.count)

        alt_ans = majority_answer(alt_runs)
        base_ans = majority_answer(base_runs)
        alt_ok = alt_ans in ("sat", "unsat", "unknown")
        base_ok = base_ans in ("sat", "unsat", "unknown")

        # Use median elapsed time
        alt_t = statistics.median(r.elapsed for r in alt_runs)
        base_t = statistics.median(r.elapsed for r in base_runs)
        short = shorten(alt_runs[0].path)

        # Unique solves / unique fails
        if alt_ok and not base_ok and base_ans == "timeout":
            self._alt_unique_solves += 1
            line = f"  UNIQUE SOLVE ({alt_t:.2f}s, {self._base_name} timed out): {short}"
            self._recent_wins.append(line)
            if len(self._recent_wins) > self._max_recent:
                self._recent_wins = self._recent_wins[-self._max_recent:]
            return

        if not alt_ok and alt_ans == "timeout" and base_ok:
            self._alt_unique_fails += 1
            line = f"  {self._alt_name} TIMEOUT ({self._base_name} did it in {base_t:.2f}s): {short}"
            self._recent_losses.append(line)
            if len(self._recent_losses) > self._max_recent:
                self._recent_losses = self._recent_losses[-self._max_recent:]
            return

        # Both solved -- compare median times
        if not alt_ok or not base_ok:
            return

        # Avoid division by zero on very fast solves
        if base_t < 0.001 and alt_t < 0.001:
            self._ties += 1
            return

        if base_t < 0.001:
            ratio = alt_t / 0.001
        else:
            ratio = alt_t / base_t

        if ratio >= self.MASSIVE_RATIO:
            self._alt_massive += 1
            self._alt_losses += 1
            line = (f"  {alt_t:.2f}s vs {self._base_name} {base_t:.2f}s "
                    f"({ratio:.0f}x slower): {short}")
            self._recent_losses.append(line)
            if len(self._recent_losses) > self._max_recent:
                self._recent_losses = self._recent_losses[-self._max_recent:]
        elif ratio >= self.LOSE_RATIO:
            self._alt_losses += 1
            line = (f"  {alt_t:.2f}s vs {self._base_name} {base_t:.2f}s "
                    f"({ratio:.0f}x slower): {short}")
            self._recent_losses.append(line)
            if len(self._recent_losses) > self._max_recent:
                self._recent_losses = self._recent_losses[-self._max_recent:]
        elif ratio <= self.WIN_RATIO:
            self._alt_wins += 1
            inv = 1.0 / ratio if ratio > 0 else 999
            line = (f"  {alt_t:.2f}s vs {self._base_name} {base_t:.2f}s "
                    f"({inv:.0f}x faster): {short}")
            self._recent_wins.append(line)
            if len(self._recent_wins) > self._max_recent:
                self._recent_wins = self._recent_wins[-self._max_recent:]
        else:
            self._ties += 1

    def update(self, r: Result):
        with self.lock:
            self.completed += 1
            s = self.per_solver[r.solver]
            s["done"] += 1
            s["total_time"] += r.elapsed
            if r.answer in ("sat", "unsat", "unknown", "timeout", "error", "crash"):
                s[r.answer] += 1
            else:
                s["other"] += 1
            if r.answer in ("sat", "unsat"):
                s["solved_time"] += r.elapsed
                s["solved"] += 1

            # Accumulate per-file, per-solver results
            if r.path not in self._file_results:
                self._file_results[r.path] = {}
                self._file_task_count[r.path] = 0
            if r.solver not in self._file_results[r.path]:
                self._file_results[r.path][r.solver] = []
            self._file_results[r.path][r.solver].append(r)
            self._file_task_count[r.path] += 1

            # Once all runs for all solvers are done, classify
            if self._file_task_count[r.path] == self._expected_per_file:
                self._classify_file(self._file_results.pop(r.path))
                del self._file_task_count[r.path]

            self._render()

    def _render(self):
        if self._lines > 0:
            sys.stderr.write(f"\033[{self._lines}A")

        elapsed = time.monotonic() - self.start_time
        lines = []
        lines.append("")
        lines.append(f"{BOLD}{'═' * 78}{RST}")
        lines.append(f"{BOLD}  STP vs Bitwuzla (UF benchmarks): {self._base_name} vs {self._alt_name}  ({self.runs} runs/file){RST}")
        lines.append(f"{'═' * 78}")

        # Progress
        pct = self.completed / self.total_tasks if self.total_tasks else 0
        bar_w = 50
        filled = int(bar_w * pct)
        bar = "█" * filled + "░" * (bar_w - filled)
        lines.append(f"  [{bar}] {pct*100:5.1f}%")
        lines.append("")

        files_done = self._files_compared
        lines.append(f"  {CYAN}Tasks:{RST}    {self.completed:>8,} / {self.total_tasks:,}")
        lines.append(f"  {CYAN}Files:{RST}    {files_done:>8,} / {self.total_files:,}  fully compared (median of {self.runs} runs)")
        lines.append("")

        # Per-solver stats
        hdr = f"  {'Solver':<12} {'Runs':>7} {'sat':>7} {'unsat':>7} {'TO':>6} {'err':>5} {'crash':>5} {'avg(s)':>7}"
        lines.append(f"{BOLD}{hdr}{RST}")
        lines.append(f"  {'─' * 74}")
        for name in self.solver_names:
            s = self.per_solver[name]
            avg = s["solved_time"] / s["solved"] if s["solved"] else 0
            lines.append(
                f"  {name:<12} {s['done']:>7,} {s['sat']:>7,} {s['unsat']:>7,} "
                f"{s['timeout']:>6,} {s['error']:>5,} {s['crash']:>5,} {avg:>7.2f}"
            )
        lines.append("")

        # comparison scoreboard
        lines.append(f"{BOLD}  {self._alt_name} Scoreboard{RST}  ({self._files_compared:,} files compared)")
        lines.append(f"  {'─' * 74}")
        lines.append(
            f"  {GREEN}Wins (2x+ faster):{RST}  {self._alt_wins:>6,}   "
            f"{YELLOW}Ties:{RST} {self._ties:>6,}   "
            f"{RED}Losses (5x+ slower):{RST} {self._alt_losses:>6,}"
        )
        lines.append(
            f"  {GREEN}Unique solves:{RST}      {self._alt_unique_solves:>6,}   "
            f"{MAGENTA}Massive (20x+):{RST} {self._alt_massive:>5,}   "
            f"{RED}Unique timeouts:{RST}    {self._alt_unique_fails:>6,}"
        )
        lines.append("")

        # Recent wins
        if self._recent_wins:
            lines.append(f"  {GREEN}{BOLD}Recent {self._alt_name} wins:{RST}")
            for w in self._recent_wins:
                lines.append(f"{GREEN}{w}{RST}")
            lines.append("")

        # Recent losses
        if self._recent_losses:
            lines.append(f"  {RED}{BOLD}Recent {self._alt_name} losses:{RST}")
            for l in self._recent_losses:
                lines.append(f"{RED}{l}{RST}")
            lines.append("")

        # Timing
        rate = self.completed / elapsed if elapsed > 0 else 0
        remaining_tasks = self.total_tasks - self.completed
        eta = remaining_tasks / rate if rate > 0 else 0

        lines.append(f"  {CYAN}Elapsed:{RST} {fmt_duration(elapsed)}   "
                      f"{CYAN}Rate:{RST} {rate:.1f} tasks/s   "
                      f"{CYAN}ETA:{RST} {fmt_duration(eta)}")
        lines.append(f"{'─' * 78}")

        output = "\n".join(CL + l for l in lines)
        sys.stderr.write(output + "\n")
        sys.stderr.flush()
        self._lines = len(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def get_git_hash(binary_path):
    """Get the short git hash from the repo containing a solver binary."""
    repo_dir = Path(binary_path).resolve().parent
    while repo_dir != repo_dir.parent:
        if (repo_dir / ".git").exists():
            break
        repo_dir = repo_dir.parent
    else:
        return "unknown"
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def main():
    parser = argparse.ArgumentParser(description="Compare STP vs Bitwuzla on UF benchmarks")
    parser.add_argument("--dir", type=Path, nargs="*", default=[DEFAULT_DIR])
    parser.add_argument("--file-list", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="Per-solver timeout in seconds (default: 30)")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS,
                        help="Number of runs per file per solver (default: 3)")
    parser.add_argument("--wall-hours", type=float, default=DEFAULT_WALL_HOURS)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--output", type=Path, default=None,
                        help="Output CSV (default: auto-generated with git hashes)")
    parser.add_argument("--incremental", action="store_true",
                        help="Feed input via stdin line-by-line (SMT-COMP trace executor style)")
    args = parser.parse_args()

    # Validate solvers and get git hashes
    solver_list = []
    git_hashes = {}
    for name, (path, extra_args) in SOLVERS.items():
        p = str(path.resolve())
        if not os.path.isfile(p):
            print(f"Warning: {name} binary not found at {p} (not yet built?)", file=sys.stderr)
        h = get_git_hash(p)
        git_hashes[name] = h
        solver_list.append((name, p, extra_args))
        print(f"  {name}: {p}  (git {h})")

    # Auto-generate output filename with git hashes
    if args.output is None:
        hash_parts = "_vs_".join(git_hashes.values())
        args.output = Path(f"stp_vs_bitwuzla_{hash_parts}.csv")

    # Collect files
    if args.file_list:
        print(f"Reading file list from {args.file_list} ...")
        files = load_file_list(args.file_list)
    else:
        files = []
        for d in args.dir:
            print(f"Collecting .smt2 files from {d} ...")
            files.extend(collect_smt2_files([d]))
        files.sort(key=lambda p: os.path.getsize(p))

    if not files:
        print("No .smt2 files found.", file=sys.stderr)
        sys.exit(1)

    # Build task list: for each file, interleave solvers and runs
    tasks = []
    for path in files:
        for run_idx in range(args.runs):
            for name, binary, extra_args in solver_list:
                tasks.append((name, binary, extra_args, path, run_idx))

    total_tasks = len(tasks)
    solver_names = [name for name, _, _ in solver_list]

    mode = "incremental (stdin)" if args.incremental else "batch (file arg)"
    print(f"Found {len(files):,} files x {len(solver_list)} solvers x {args.runs} runs = {total_tasks:,} tasks")
    print(f"Workers: {args.workers}, timeout: {args.timeout:.0f}s, wall budget: {args.wall_hours}h, mode: {mode}")

    wall_seconds = args.wall_hours * 3600
    start_time = time.monotonic()
    tui = TUI(total_tasks, len(files), solver_names, args.runs)
    log = ResultLog(args.output)

    print(f"Output: {args.output}")
    print(f"Starting run...\n")

    tui.start()

    def on_result(result):
        log.record(result)
        tui.update(result)

    try:
        completed_count = run_pool(tasks, args.workers, args.timeout, on_result,
                                   wall_seconds=wall_seconds,
                                   incremental=args.incremental)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
        completed_count = tui.completed
    finally:
        tui.stop()

    # ── Revalidation pass ────────────────────────────────────────────────────
    REVALIDATE_RATIO = 3.0
    REVALIDATE_TIMEOUT = max(args.timeout * 4, 120.0)

    # Read the CSV back and compute medians per (file, solver)
    per_file_runs: dict[str, dict[str, list[float]]] = {}
    per_file_answers: dict[str, dict[str, list[str]]] = {}
    with open(args.output, newline="") as f:
        for row in csv.DictReader(f):
            s = row["solver"]
            p = row["path"]
            per_file_runs.setdefault(p, {}).setdefault(s, []).append(float(row["elapsed"]))
            per_file_answers.setdefault(p, {}).setdefault(s, []).append(row["answer"])

    def median_answer(answers):
        return max(set(answers), key=answers.count)

    base_name = solver_names[0]
    alt_name = solver_names[1]

    revalidate_files: list[str] = []
    for path in per_file_runs:
        m_times = per_file_runs[path].get(base_name, [])
        q_times = per_file_runs[path].get(alt_name, [])
        m_answers = per_file_answers[path].get(base_name, [])
        q_answers = per_file_answers[path].get(alt_name, [])
        if not m_times or not q_times:
            continue

        m_ans = median_answer(m_answers)
        q_ans = median_answer(q_answers)
        m_t = statistics.median(m_times)
        q_t = statistics.median(q_times)

        m_ok = m_ans in ("sat", "unsat", "unknown")
        q_ok = q_ans in ("sat", "unsat", "unknown")

        if m_ans == "timeout" and q_ans == "timeout":
            revalidate_files.append(path)
            continue
        if m_ok and q_ans == "timeout":
            revalidate_files.append(path)
            continue
        if q_ok and m_ans == "timeout":
            revalidate_files.append(path)
            continue

        if m_ok and q_ok:
            denom = max(m_t, 0.001)
            ratio = q_t / denom
            if ratio >= REVALIDATE_RATIO or ratio <= 1.0 / REVALIDATE_RATIO:
                revalidate_files.append(path)

    revalidate_files.sort(key=lambda p: os.path.getsize(p))

    if revalidate_files:
        reval_tasks = []
        for path in revalidate_files:
            for run_idx in range(args.runs):
                for name, binary, extra_args in solver_list:
                    reval_tasks.append((name, binary, extra_args, path, run_idx))

        total_reval = len(reval_tasks)
        reval_solver_names = [name for name, _, _ in solver_list]

        reval_output = args.output.parent / (args.output.stem + "_revalidation" + args.output.suffix)

        print(f"\n{'=' * 72}")
        print(f"  REVALIDATION: {len(revalidate_files):,} files flagged (>= {REVALIDATE_RATIO:.0f}x either way)")
        print(f"  {total_reval:,} tasks, timeout {REVALIDATE_TIMEOUT:.0f}s, {args.runs} runs/file")
        print(f"  Output: {reval_output}")
        print(f"{'=' * 72}\n")

        reval_log = ResultLog(reval_output)
        reval_tui = TUI(total_reval, len(revalidate_files), reval_solver_names, args.runs)
        reval_tui.start()

        def on_reval_result(result):
            reval_log.record(result)
            reval_tui.update(result)

        try:
            reval_completed = run_pool(reval_tasks, args.workers, REVALIDATE_TIMEOUT,
                                       on_reval_result,
                                       incremental=args.incremental)
        except KeyboardInterrupt:
            print("\n\nRevalidation interrupted.")
            reval_completed = reval_tui.completed
        finally:
            reval_tui.stop()

        print(f"\n  Revalidation: {reval_completed:,} / {total_reval:,} tasks completed")
    else:
        print(f"\n  No files exceeded {REVALIDATE_RATIO:.0f}x threshold — revalidation skipped.")

    # ── Final summary ────────────────────────────────────────────────────────

    elapsed = time.monotonic() - start_time

    print(f"\n{'=' * 72}")
    print(f"  COMPARISON COMPLETE")
    print(f"{'=' * 72}")
    print(f"  Tasks completed: {completed_count:,} / {total_tasks:,}")
    if revalidate_files:
        print(f"  Revalidated:     {len(revalidate_files):,} files")
    print(f"  Elapsed: {fmt_duration(elapsed)}")
    print(f"  Results: {args.output}")
    if revalidate_files:
        print(f"  Revalidation: {reval_output}")
    print(f"{'=' * 72}")

    # Competition-style score tables
    print()
    if revalidate_files:
        from benchlib import load_combined
        final_data = load_combined(str(args.output), str(reval_output))
    else:
        final_data = load_medians(str(args.output))
    final_paired = pair_files(final_data, solver_names[0], solver_names[1])
    for vto in [24, 120]:
        score_table(final_paired, solver_names[0], solver_names[1], vto)


if __name__ == "__main__":
    main()
