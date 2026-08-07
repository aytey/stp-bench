#!/usr/bin/env python3
"""
Compare STP build performance: master vs incremental-solving branch.
Runs incremental .smt2 files against both builds multiple times, records
timing, and uses median times for comparison.

Output: a CSV with one row per (file, solver, run) triple.
"""

import argparse
import csv
import os
import statistics
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

# ── Defaults ──────────────────────────────────────────────────────────────────

SOLVERS = {
    "master":       (Path("/home/avj/clones/stp/master/build/stp"), ["--array-equality"]),
    "incremental":  (Path("/home/avj/clones/stp/incremental-solving/build/stp"), ["--array-equality"]),
}

DEFAULT_DIR = Path("/mnt/baranem/smt2_problems/incremental")

DEFAULT_TIMEOUT = 30.0  # 30 seconds
DEFAULT_WALL_HOURS = 24.0
DEFAULT_WORKERS = os.cpu_count() or 1
DEFAULT_RUNS = 3

# ── Data ──────────────────────────────────────────────────────────────────────

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


def run_one(solver_name: str, solver_bin: str, extra_args: list[str],
            path: str, run: int, timeout: float) -> Result:
    """Run a solver on a single .smt2 file and record the result."""
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [solver_bin] + extra_args + [path],
            capture_output=True,
            timeout=timeout,
            text=True,
        )
        elapsed = time.monotonic() - t0
        first_line = proc.stdout.strip().split("\n")[0].strip() if proc.stdout else ""
        sig = -proc.returncode if proc.returncode < 0 else 0

        if proc.returncode < 0:
            answer = "crash"
        elif proc.returncode != 0:
            answer = "error"
        elif first_line in ("sat", "unsat", "unknown"):
            answer = first_line
        else:
            answer = f"other:{first_line[:80]}"

        return Result(path, solver_name, run, elapsed, answer, proc.returncode, sig, timeout)

    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        return Result(path, solver_name, run, elapsed, "timeout", -1, 0, timeout)
    except Exception as e:
        elapsed = time.monotonic() - t0
        return Result(path, solver_name, run, elapsed, "error", -1, 0, timeout)


# ── TUI ───────────────────────────────────────────────────────────────────────

class TUI:
    CL = "\033[2K"
    HIDE = "\033[?25l"
    SHOW = "\033[?25h"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    DIM = "\033[2m"
    RST = "\033[0m"

    # Thresholds for classification
    WIN_RATIO = 0.5     # incremental is 2x+ faster → win
    LOSE_RATIO = 5.0    # incremental is 5x+ slower → loss
    MASSIVE_RATIO = 20.0  # incremental is 20x+ slower → massive loss

    def __init__(self, total_tasks: int, total_files: int,
                 solver_names: list[str], runs: int):
        self.total_tasks = total_tasks
        self.total_files = total_files
        self.solver_names = solver_names
        self.runs = runs
        self.completed = 0
        self.per_solver = {s: {"done": 0, "sat": 0, "unsat": 0, "unknown": 0,
                               "timeout": 0, "error": 0, "crash": 0, "other": 0,
                               "total_time": 0.0, "solved_time": 0.0, "solved": 0}
                           for s in solver_names}
        self.start_time = time.monotonic()
        self.lock = Lock()
        self._lines = 0

        # Per-file result accumulation: path → solver → list[Result]
        self._file_results: dict[str, dict[str, list[Result]]] = {}
        self._expected_per_file = len(solver_names) * runs
        self._file_task_count: dict[str, int] = {}
        self._files_compared = 0
        self._incr_wins = 0
        self._incr_losses = 0
        self._incr_massive = 0
        self._ties = 0
        self._incr_unique_solves = 0
        self._incr_unique_fails = 0

        # Recent highlights
        self._recent_wins: list[str] = []
        self._recent_losses: list[str] = []
        self._max_recent = 5

    def start(self):
        sys.stderr.write(self.HIDE)
        sys.stderr.flush()

    def stop(self):
        sys.stderr.write(self.SHOW)
        sys.stderr.flush()

    def _shorten(self, path: str) -> str:
        for tag in ("non-incremental/", "incremental/"):
            if tag in path:
                return path.split(tag, 1)[-1]
        return os.path.basename(path)

    def _classify_file(self, file_results: dict[str, list[Result]]):
        """Once all runs for both builds are done, classify using medians."""
        self._files_compared += 1

        incr_runs = file_results.get("incremental", [])
        master_runs = file_results.get("master", [])
        if not incr_runs or not master_runs:
            return

        # Use majority answer (most common across runs)
        def majority_answer(runs):
            answers = [r.answer for r in runs]
            return max(set(answers), key=answers.count)

        incr_ans = majority_answer(incr_runs)
        master_ans = majority_answer(master_runs)
        incr_ok = incr_ans in ("sat", "unsat", "unknown")
        master_ok = master_ans in ("sat", "unsat", "unknown")

        # Use median elapsed time
        incr_t = statistics.median(r.elapsed for r in incr_runs)
        master_t = statistics.median(r.elapsed for r in master_runs)
        short = self._shorten(incr_runs[0].path)

        # Unique solves / unique fails
        if incr_ok and not master_ok and master_ans == "timeout":
            self._incr_unique_solves += 1
            line = f"  UNIQUE SOLVE ({incr_t:.2f}s, master timed out): {short}"
            self._recent_wins.append(line)
            if len(self._recent_wins) > self._max_recent:
                self._recent_wins = self._recent_wins[-self._max_recent:]
            return

        if not incr_ok and incr_ans == "timeout" and master_ok:
            self._incr_unique_fails += 1
            line = f"  incremental TIMEOUT (master did it in {master_t:.2f}s): {short}"
            self._recent_losses.append(line)
            if len(self._recent_losses) > self._max_recent:
                self._recent_losses = self._recent_losses[-self._max_recent:]
            return

        # Both solved — compare median times
        if not incr_ok or not master_ok:
            return

        # Avoid division by zero on very fast solves
        if master_t < 0.001 and incr_t < 0.001:
            self._ties += 1
            return

        if master_t < 0.001:
            ratio = incr_t / 0.001
        else:
            ratio = incr_t / master_t

        if ratio >= self.MASSIVE_RATIO:
            self._incr_massive += 1
            self._incr_losses += 1
            line = (f"  {incr_t:.2f}s vs master {master_t:.2f}s "
                    f"({ratio:.0f}x slower): {short}")
            self._recent_losses.append(line)
            if len(self._recent_losses) > self._max_recent:
                self._recent_losses = self._recent_losses[-self._max_recent:]
        elif ratio >= self.LOSE_RATIO:
            self._incr_losses += 1
            line = (f"  {incr_t:.2f}s vs master {master_t:.2f}s "
                    f"({ratio:.0f}x slower): {short}")
            self._recent_losses.append(line)
            if len(self._recent_losses) > self._max_recent:
                self._recent_losses = self._recent_losses[-self._max_recent:]
        elif ratio <= self.WIN_RATIO:
            self._incr_wins += 1
            inv = 1.0 / ratio if ratio > 0 else 999
            line = (f"  {incr_t:.2f}s vs master {master_t:.2f}s "
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
        lines.append(f"{self.BOLD}{'═' * 78}{self.RST}")
        lines.append(f"{self.BOLD}  STP Build Comparison: master vs incremental  ({self.runs} runs/file){self.RST}")
        lines.append(f"{'═' * 78}")

        # Progress
        pct = self.completed / self.total_tasks if self.total_tasks else 0
        bar_w = 50
        filled = int(bar_w * pct)
        bar = "█" * filled + "░" * (bar_w - filled)
        lines.append(f"  [{bar}] {pct*100:5.1f}%")
        lines.append("")

        files_done = self._files_compared
        lines.append(f"  {self.CYAN}Tasks:{self.RST}    {self.completed:>8,} / {self.total_tasks:,}")
        lines.append(f"  {self.CYAN}Files:{self.RST}    {files_done:>8,} / {self.total_files:,}  fully compared (median of {self.runs} runs)")
        lines.append("")

        # Per-solver stats
        hdr = f"  {'Build':<12} {'Runs':>7} {'sat':>7} {'unsat':>7} {'TO':>6} {'err':>5} {'crash':>5} {'avg(s)':>7}"
        lines.append(f"{self.BOLD}{hdr}{self.RST}")
        lines.append(f"  {'─' * 74}")
        for name in self.solver_names:
            s = self.per_solver[name]
            avg = s["solved_time"] / s["solved"] if s["solved"] else 0
            lines.append(
                f"  {name:<12} {s['done']:>7,} {s['sat']:>7,} {s['unsat']:>7,} "
                f"{s['timeout']:>6,} {s['error']:>5,} {s['crash']:>5,} {avg:>7.2f}"
            )
        lines.append("")

        # incremental comparison scoreboard
        lines.append(f"{self.BOLD}  incremental Scoreboard{self.RST}  ({self._files_compared:,} files compared)")
        lines.append(f"  {'─' * 74}")
        lines.append(
            f"  {self.GREEN}Wins (2x+ faster):{self.RST}  {self._incr_wins:>6,}   "
            f"{self.YELLOW}Ties:{self.RST} {self._ties:>6,}   "
            f"{self.RED}Losses (5x+ slower):{self.RST} {self._incr_losses:>6,}"
        )
        lines.append(
            f"  {self.GREEN}Unique solves:{self.RST}      {self._incr_unique_solves:>6,}   "
            f"{self.MAGENTA}Massive (20x+):{self.RST} {self._incr_massive:>5,}   "
            f"{self.RED}Unique timeouts:{self.RST}    {self._incr_unique_fails:>6,}"
        )
        lines.append("")

        # Recent wins
        if self._recent_wins:
            lines.append(f"  {self.GREEN}{self.BOLD}Recent incremental wins:{self.RST}")
            for w in self._recent_wins:
                lines.append(f"{self.GREEN}{w}{self.RST}")
            lines.append("")

        # Recent losses
        if self._recent_losses:
            lines.append(f"  {self.RED}{self.BOLD}Recent incremental losses:{self.RST}")
            for l in self._recent_losses:
                lines.append(f"{self.RED}{l}{self.RST}")
            lines.append("")

        # Timing
        rate = self.completed / elapsed if elapsed > 0 else 0
        remaining_tasks = self.total_tasks - self.completed
        eta = remaining_tasks / rate if rate > 0 else 0

        def fmt(secs):
            h = int(secs // 3600)
            m = int((secs % 3600) // 60)
            s = int(secs % 60)
            return f"{h}h{m:02d}m{s:02d}s" if h else (f"{m}m{s:02d}s" if m else f"{s}s")

        lines.append(f"  {self.CYAN}Elapsed:{self.RST} {fmt(elapsed)}   "
                      f"{self.CYAN}Rate:{self.RST} {rate:.1f} tasks/s   "
                      f"{self.CYAN}ETA:{self.RST} {fmt(eta)}")
        lines.append(f"{'─' * 78}")

        output = "\n".join(self.CL + l for l in lines)
        sys.stderr.write(output + "\n")
        sys.stderr.flush()
        self._lines = len(lines)


# ── CSV output ────────────────────────────────────────────────────────────────

class ResultLog:
    HEADER = ["path", "solver", "run", "elapsed", "answer", "exit_code", "signal", "timeout"]

    def __init__(self, path: Path):
        self.path = path
        self.lock = Lock()
        with open(self.path, "w", newline="") as f:
            csv.writer(f).writerow(self.HEADER)

    def record(self, r: Result):
        with self.lock:
            with open(self.path, "a", newline="") as f:
                csv.writer(f).writerow([
                    r.path, r.solver, r.run, f"{r.elapsed:.3f}",
                    r.answer, r.exit_code, r.signal_num, f"{r.timeout_used:.0f}"
                ])


# ── Main ──────────────────────────────────────────────────────────────────────

def collect_files(directories: list[Path]) -> list[str]:
    files = []
    for d in directories:
        for root, _, names in os.walk(d):
            for name in names:
                if name.endswith(".smt2"):
                    files.append(os.path.join(root, name))
    files.sort(key=lambda p: os.path.getsize(p))
    return files


def main():
    parser = argparse.ArgumentParser(description="Compare STP build performance: master vs incremental-solving")
    parser.add_argument("--dir", type=Path, nargs="*", default=[DEFAULT_DIR])
    parser.add_argument("--file-list", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="Per-solver timeout in seconds (default: 30)")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS,
                        help="Number of runs per file per solver (default: 3)")
    parser.add_argument("--wall-hours", type=float, default=DEFAULT_WALL_HOURS)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--output", type=Path, default=Path("build_comparison.csv"))
    args = parser.parse_args()

    # Validate solvers
    solver_list = []
    for name, (path, extra_args) in SOLVERS.items():
        p = str(path.resolve())
        if not os.path.isfile(p):
            print(f"Warning: {name} binary not found at {p} (not yet built?)", file=sys.stderr)
        solver_list.append((name, p, extra_args))

    # Collect files
    if args.file_list:
        print(f"Reading file list from {args.file_list} ...")
        files = []
        with open(args.file_list) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    files.append(line)
        files.sort(key=lambda p: os.path.getsize(p))
    else:
        files = []
        for d in args.dir:
            print(f"Collecting .smt2 files from {d} ...")
            files.extend(collect_files([d]))
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

    print(f"Found {len(files):,} files × {len(solver_list)} builds × {args.runs} runs = {total_tasks:,} tasks")
    print(f"Workers: {args.workers}, timeout: {args.timeout:.0f}s, wall budget: {args.wall_hours}h")

    wall_seconds = args.wall_hours * 3600
    start_time = time.monotonic()
    tui = TUI(total_tasks, len(files), solver_names, args.runs)
    log = ResultLog(args.output)

    print(f"Output: {args.output}")
    print(f"Starting run...\n")

    tui.start()
    completed_count = 0

    try:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            BATCH = args.workers * 4
            task_iter = iter(tasks)
            pending = {}
            exhausted = False

            # Seed
            for _ in range(min(BATCH, total_tasks)):
                try:
                    name, binary, extra_args, path, run_idx = next(task_iter)
                    fut = executor.submit(run_one, name, binary, extra_args, path, run_idx, args.timeout)
                    pending[fut] = True
                except StopIteration:
                    exhausted = True
                    break

            while pending:
                elapsed = time.monotonic() - start_time
                if elapsed >= wall_seconds:
                    for fut in pending:
                        fut.cancel()
                    break

                batch = []
                try:
                    for fut in as_completed(pending, timeout=1.0):
                        batch.append(fut)
                        if len(batch) >= args.workers:
                            break
                except TimeoutError:
                    pass

                for fut in batch:
                    try:
                        result = fut.result()
                        log.record(result)
                        tui.update(result)
                        completed_count += 1
                    except Exception:
                        pass
                    del pending[fut]

                    if not exhausted:
                        try:
                            name, binary, extra_args, path, run_idx = next(task_iter)
                            new_fut = executor.submit(run_one, name, binary, extra_args, path, run_idx, args.timeout)
                            pending[new_fut] = True
                        except StopIteration:
                            exhausted = True

    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
    finally:
        tui.stop()

    # ── Revalidation pass ────────────────────────────────────────────────────
    # Re-run any file where the median ratio was >= 3x either way (or one
    # build uniquely solved / timed out).  Uses a longer timeout.

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

    revalidate_files: list[str] = []
    for path in per_file_runs:
        m_times = per_file_runs[path].get("master", [])
        q_times = per_file_runs[path].get("incremental", [])
        m_answers = per_file_answers[path].get("master", [])
        q_answers = per_file_answers[path].get("incremental", [])
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
        reval_completed = 0

        try:
            with ProcessPoolExecutor(max_workers=args.workers) as executor:
                BATCH = args.workers * 4
                task_iter = iter(reval_tasks)
                pending = {}
                exhausted = False

                for _ in range(min(BATCH, total_reval)):
                    try:
                        name, binary, extra_args, path, run_idx = next(task_iter)
                        fut = executor.submit(run_one, name, binary, extra_args, path, run_idx, REVALIDATE_TIMEOUT)
                        pending[fut] = True
                    except StopIteration:
                        exhausted = True
                        break

                while pending:
                    batch = []
                    try:
                        for fut in as_completed(pending, timeout=1.0):
                            batch.append(fut)
                            if len(batch) >= args.workers:
                                break
                    except TimeoutError:
                        pass

                    for fut in batch:
                        try:
                            result = fut.result()
                            reval_log.record(result)
                            reval_tui.update(result)
                            reval_completed += 1
                        except Exception:
                            pass
                        del pending[fut]

                        if not exhausted:
                            try:
                                name, binary, extra_args, path, run_idx = next(task_iter)
                                new_fut = executor.submit(run_one, name, binary, extra_args, path, run_idx, REVALIDATE_TIMEOUT)
                                pending[new_fut] = True
                            except StopIteration:
                                exhausted = True

        except KeyboardInterrupt:
            print("\n\nRevalidation interrupted.")
        finally:
            reval_tui.stop()

        print(f"\n  Revalidation: {reval_completed:,} / {total_reval:,} tasks completed")
    else:
        print(f"\n  No files exceeded {REVALIDATE_RATIO:.0f}x threshold — revalidation skipped.")

    # ── Final summary ────────────────────────────────────────────────────────

    elapsed = time.monotonic() - start_time

    def fmt(secs):
        h = int(secs // 3600)
        m = int((secs % 3600) // 60)
        s = int(secs % 60)
        return f"{h}h{m:02d}m{s:02d}s"

    print(f"\n{'=' * 72}")
    print(f"  COMPARISON COMPLETE")
    print(f"{'=' * 72}")
    print(f"  Tasks completed: {completed_count:,} / {total_tasks:,}")
    if revalidate_files:
        print(f"  Revalidated:     {len(revalidate_files):,} files")
    print(f"  Elapsed: {fmt(elapsed)}")
    print(f"  Results: {args.output}")
    if revalidate_files:
        print(f"  Revalidation: {reval_output}")
    print(f"{'=' * 72}")


if __name__ == "__main__":
    main()
