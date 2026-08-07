#!/usr/bin/env python3
"""
Compare solver performance: cvc5 vs bitwuzla vs stp (symfpu_rebased).
Runs all .smt2 files against each solver, records timing, and identifies
where STP is slower.

Output: a CSV with one row per (file, solver) pair, plus a summary report.
"""

import argparse
import csv
import os
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

# ── Defaults ──────────────────────────────────────────────────────────────────

SOLVERS = {
    "stp":      Path("/home/avj/clones/stp/symfpu_rebased/build/stp_simple"),
    "cvc5":     Path("/home/avj/clones/cvc5/main/build/bin/cvc5"),
    "bitwuzla": Path("/home/avj/clones/bitwuzla/build/src/main/bitwuzla"),
}

DEFAULT_DIRS = [
    Path("/home/avj/clones/stp/non-incremental"),
    Path("/home/avj/clones/stp/incremental"),
]

DEFAULT_TIMEOUT = 300.0  # 5 minutes
DEFAULT_WALL_HOURS = 24.0
DEFAULT_WORKERS = os.cpu_count() or 1

# ── Data ──────────────────────────────────────────────────────────────────────

@dataclass
class Result:
    path: str
    solver: str
    elapsed: float
    answer: str       # "sat", "unsat", "timeout", "error", "crash", or raw first line
    exit_code: int
    signal_num: int


def run_one(solver_name: str, solver_bin: str, path: str, timeout: float) -> Result:
    """Run a solver on a single .smt2 file and record the result."""
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [solver_bin, path],
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

        return Result(path, solver_name, elapsed, answer, proc.returncode, sig)

    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        return Result(path, solver_name, elapsed, "timeout", -1, 0)
    except Exception as e:
        elapsed = time.monotonic() - t0
        return Result(path, solver_name, elapsed, "error", -1, 0)


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
    WIN_RATIO = 0.5     # STP is 2x+ faster → win
    LOSE_RATIO = 5.0    # STP is 5x+ slower → loss
    MASSIVE_RATIO = 20.0  # STP is 20x+ slower → massive loss

    def __init__(self, total_tasks: int, total_files: int, solver_names: list[str]):
        self.total_tasks = total_tasks
        self.total_files = total_files
        self.solver_names = solver_names
        self.completed = 0
        self.per_solver = {s: {"done": 0, "sat": 0, "unsat": 0, "unknown": 0,
                               "timeout": 0, "error": 0, "crash": 0, "other": 0,
                               "total_time": 0.0}
                           for s in solver_names}
        self.start_time = time.monotonic()
        self.lock = Lock()
        self._lines = 0

        # Per-file result accumulation
        self._file_results: dict[str, dict[str, Result]] = {}
        self._files_compared = 0
        self._stp_wins = 0
        self._stp_losses = 0
        self._stp_massive = 0
        self._ties = 0
        self._stp_unique_solves = 0   # STP solved, others timed out
        self._stp_unique_fails = 0    # others solved, STP timed out

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

    def _classify_file(self, file_results: dict[str, Result]):
        """Once all solvers are done for a file, classify the comparison."""
        self._files_compared += 1

        stp_r = file_results.get("stp")
        if not stp_r:
            return

        others = {k: v for k, v in file_results.items() if k != "stp"}
        if not others:
            return

        stp_ok = stp_r.answer in ("sat", "unsat", "unknown")
        stp_t = stp_r.elapsed
        short = self._shorten(stp_r.path)

        # Check for unique solves / unique fails
        others_ok = {k: v for k, v in others.items() if v.answer in ("sat", "unsat", "unknown")}
        others_to = {k: v for k, v in others.items() if v.answer == "timeout"}

        if stp_ok and len(others_ok) == 0 and len(others_to) > 0:
            self._stp_unique_solves += 1
            line = f"  UNIQUE SOLVE ({stp_t:.2f}s, others timed out): {short}"
            self._recent_wins.append(line)
            if len(self._recent_wins) > self._max_recent:
                self._recent_wins = self._recent_wins[-self._max_recent:]
            return

        if not stp_ok and stp_r.answer == "timeout" and len(others_ok) > 0:
            self._stp_unique_fails += 1
            best_other = min(others_ok.values(), key=lambda r: r.elapsed)
            line = f"  STP TIMEOUT ({best_other.solver} did it in {best_other.elapsed:.2f}s): {short}"
            self._recent_losses.append(line)
            if len(self._recent_losses) > self._max_recent:
                self._recent_losses = self._recent_losses[-self._max_recent:]
            return

        # Both STP and at least one other solved it — compare times
        if not stp_ok or not others_ok:
            return

        best_other = min(others_ok.values(), key=lambda r: r.elapsed)
        best_t = best_other.elapsed

        # Avoid division by zero on very fast solves
        if best_t < 0.001 and stp_t < 0.001:
            self._ties += 1
            return

        if best_t < 0.001:
            ratio = stp_t / 0.001
        else:
            ratio = stp_t / best_t

        if ratio >= self.MASSIVE_RATIO:
            self._stp_massive += 1
            self._stp_losses += 1
            line = (f"  {stp_t:.2f}s vs {best_other.solver} {best_t:.2f}s "
                    f"({ratio:.0f}x slower): {short}")
            self._recent_losses.append(line)
            if len(self._recent_losses) > self._max_recent:
                self._recent_losses = self._recent_losses[-self._max_recent:]
        elif ratio >= self.LOSE_RATIO:
            self._stp_losses += 1
            line = (f"  {stp_t:.2f}s vs {best_other.solver} {best_t:.2f}s "
                    f"({ratio:.0f}x slower): {short}")
            self._recent_losses.append(line)
            if len(self._recent_losses) > self._max_recent:
                self._recent_losses = self._recent_losses[-self._max_recent:]
        elif ratio <= self.WIN_RATIO:
            self._stp_wins += 1
            inv = 1.0 / ratio if ratio > 0 else 999
            line = (f"  {stp_t:.2f}s vs {best_other.solver} {best_t:.2f}s "
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

            # Accumulate per-file results
            if r.path not in self._file_results:
                self._file_results[r.path] = {}
            self._file_results[r.path][r.solver] = r

            # Once all solvers done for this file, classify
            if len(self._file_results[r.path]) == len(self.solver_names):
                self._classify_file(self._file_results.pop(r.path))

            self._render()

    def _render(self):
        if self._lines > 0:
            sys.stderr.write(f"\033[{self._lines}A")

        elapsed = time.monotonic() - self.start_time
        lines = []
        lines.append("")
        lines.append(f"{self.BOLD}{'═' * 78}{self.RST}")
        lines.append(f"{self.BOLD}  Solver Comparison: stp vs cvc5 vs bitwuzla{self.RST}")
        lines.append(f"{'═' * 78}")

        # Progress
        pct = self.completed / self.total_tasks if self.total_tasks else 0
        bar_w = 50
        filled = int(bar_w * pct)
        bar = "█" * filled + "░" * (bar_w - filled)
        lines.append(f"  [{bar}] {pct*100:5.1f}%")
        lines.append("")

        files_done = min(s["done"] for s in self.per_solver.values())
        lines.append(f"  {self.CYAN}Tasks:{self.RST}    {self.completed:>8,} / {self.total_tasks:,}")
        lines.append(f"  {self.CYAN}Files:{self.RST}    {files_done:>8,} / {self.total_files:,}  complete across all solvers")
        lines.append("")

        # Per-solver stats
        hdr = f"  {'Solver':<12} {'Done':>7} {'sat':>7} {'unsat':>7} {'TO':>6} {'err':>5} {'crash':>5} {'avg(s)':>7}"
        lines.append(f"{self.BOLD}{hdr}{self.RST}")
        lines.append(f"  {'─' * 74}")
        for name in self.solver_names:
            s = self.per_solver[name]
            avg = s["total_time"] / s["done"] if s["done"] else 0
            lines.append(
                f"  {name:<12} {s['done']:>7,} {s['sat']:>7,} {s['unsat']:>7,} "
                f"{s['timeout']:>6,} {s['error']:>5,} {s['crash']:>5,} {avg:>7.2f}"
            )
        lines.append("")

        # STP comparison scoreboard
        lines.append(f"{self.BOLD}  STP Scoreboard{self.RST}  ({self._files_compared:,} files compared)")
        lines.append(f"  {'─' * 74}")
        lines.append(
            f"  {self.GREEN}Wins (2x+ faster):{self.RST}  {self._stp_wins:>6,}   "
            f"{self.YELLOW}Ties:{self.RST} {self._ties:>6,}   "
            f"{self.RED}Losses (5x+ slower):{self.RST} {self._stp_losses:>6,}"
        )
        lines.append(
            f"  {self.GREEN}Unique solves:{self.RST}      {self._stp_unique_solves:>6,}   "
            f"{self.MAGENTA}Massive (20x+):{self.RST} {self._stp_massive:>5,}   "
            f"{self.RED}Unique timeouts:{self.RST}    {self._stp_unique_fails:>6,}"
        )
        lines.append("")

        # Recent wins
        if self._recent_wins:
            lines.append(f"  {self.GREEN}{self.BOLD}Recent STP wins:{self.RST}")
            for w in self._recent_wins:
                lines.append(f"{self.GREEN}{w}{self.RST}")
            lines.append("")

        # Recent losses
        if self._recent_losses:
            lines.append(f"  {self.RED}{self.BOLD}Recent STP losses:{self.RST}")
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
    HEADER = ["path", "solver", "elapsed", "answer", "exit_code", "signal"]

    def __init__(self, path: Path):
        self.path = path
        self.lock = Lock()
        with open(self.path, "w", newline="") as f:
            csv.writer(f).writerow(self.HEADER)

    def record(self, r: Result):
        with self.lock:
            with open(self.path, "a", newline="") as f:
                csv.writer(f).writerow([
                    r.path, r.solver, f"{r.elapsed:.3f}",
                    r.answer, r.exit_code, r.signal_num
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
    parser = argparse.ArgumentParser(description="Compare SMT solver performance")
    parser.add_argument("--dir", type=Path, nargs="*", default=DEFAULT_DIRS)
    parser.add_argument("--file-list", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="Per-solver timeout in seconds (default: 300)")
    parser.add_argument("--wall-hours", type=float, default=DEFAULT_WALL_HOURS)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--output", type=Path, default=Path("solver_comparison.csv"))
    args = parser.parse_args()

    # Validate solvers
    solver_list = []
    for name, path in SOLVERS.items():
        p = str(path.resolve())
        if not os.path.isfile(p):
            print(f"Error: {name} binary not found at {p}", file=sys.stderr)
            sys.exit(1)
        solver_list.append((name, p))

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

    # Build task list: interleave solvers per file so we get comparable data
    # for each file as we go
    tasks = []
    for path in files:
        for name, binary in solver_list:
            tasks.append((name, binary, path))

    total_tasks = len(tasks)
    solver_names = [name for name, _ in solver_list]

    print(f"Found {len(files):,} files × {len(solver_list)} solvers = {total_tasks:,} tasks")
    print(f"Workers: {args.workers}, timeout: {args.timeout:.0f}s, wall budget: {args.wall_hours}h")

    wall_seconds = args.wall_hours * 3600
    start_time = time.monotonic()
    tui = TUI(total_tasks, len(files), solver_names)
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
                    name, binary, path = next(task_iter)
                    fut = executor.submit(run_one, name, binary, path, args.timeout)
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
                            name, binary, path = next(task_iter)
                            new_fut = executor.submit(run_one, name, binary, path, args.timeout)
                            pending[new_fut] = True
                        except StopIteration:
                            exhausted = True

    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
    finally:
        tui.stop()

    # Final summary
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
    print(f"  Elapsed: {fmt(elapsed)}")
    print(f"  Results: {args.output}")
    print(f"{'=' * 72}")


if __name__ == "__main__":
    main()
