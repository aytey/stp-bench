#!/usr/bin/env python3
"""
Run all .smt2 files against STP (symfpu_rebased build).
Supports both non-incremental and incremental benchmarks.
Adaptive timeout to fit within a 12h wall-clock budget.
TUI showing progress, errors, and current timeout.
"""

import argparse
import csv
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_STP = Path(__file__).parent / "symfpu_rebased" / "build" / "stp_simple"
DEFAULT_DIRS = [
    Path(__file__).parent / "non-incremental",
    Path(__file__).parent / "incremental",
]
DEFAULT_WALL_HOURS = 12.0
DEFAULT_ESTIMATED_HOURS = 48.0  # oversubscribe initially
DEFAULT_WORKERS = os.cpu_count() or 1
MIN_TIMEOUT = 2.0
MAX_TIMEOUT = 600.0

# ── Data ──────────────────────────────────────────────────────────────────────

EXPECTED_RE = re.compile(r'\(set-info\s+:status\s+(sat|unsat|unknown)\)')

@dataclass
class Result:
    path: str
    outcome: str        # "ok", "wrong_answer", "crash", "timeout", "error"
    exit_code: int
    elapsed: float
    expected: str       # "sat" / "unsat" / "unknown" / ""
    actual: str         # stdout first line
    stderr_head: str    # first 200 chars of stderr
    signal_num: int = 0


def extract_expected_list(path: str) -> list[str]:
    """Read the file to find all (set-info :status ...) values, in order."""
    try:
        with open(path, "r", errors="replace") as f:
            content = f.read()
        return EXPECTED_RE.findall(content)
    except OSError:
        return []


def run_one(stp_bin: str, path: str, timeout: float, extra_args: list[str] = ()) -> Result:
    """Run STP on a single .smt2 file and classify the result."""
    expected_list = extract_expected_list(path)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [stp_bin, *extra_args, path],
            capture_output=True,
            timeout=timeout,
            text=True,
        )
        elapsed = time.monotonic() - t0
        actual_lines = [l.strip() for l in proc.stdout.strip().split("\n") if l.strip()] if proc.stdout else []
        stderr_head = (proc.stderr or "")[:200]
        sig = -proc.returncode if proc.returncode < 0 else 0

        if proc.returncode < 0:
            outcome = "crash"
            expected_str = ",".join(expected_list) if expected_list else ""
            actual_str = ",".join(actual_lines) if actual_lines else ""
        elif proc.returncode != 0:
            outcome = "error"
            expected_str = ",".join(expected_list) if expected_list else ""
            actual_str = ",".join(actual_lines) if actual_lines else ""
        else:
            # Check each expected/actual pair for wrong answers
            outcome = "ok"
            mismatches = []
            for i, exp in enumerate(expected_list):
                if exp in ("sat", "unsat") and i < len(actual_lines):
                    if actual_lines[i] != exp:
                        mismatches.append(f"{i}:expected={exp},got={actual_lines[i]}")
            if mismatches:
                outcome = "wrong_answer"

            expected_str = ",".join(expected_list) if expected_list else ""
            actual_str = ",".join(actual_lines) if actual_lines else ""

        return Result(path, outcome, proc.returncode, elapsed,
                      expected_str, actual_str, stderr_head, sig)

    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        expected_str = ",".join(expected_list) if expected_list else ""
        return Result(path, "timeout", -1, elapsed, expected_str, "", "", 0)
    except Exception as e:
        elapsed = time.monotonic() - t0
        expected_str = ",".join(expected_list) if expected_list else ""
        return Result(path, "error", -1, elapsed, expected_str, "", str(e)[:200], 0)


# ── Adaptive timeout ─────────────────────────────────────────────────────────

class TimeoutManager:
    """
    Computes per-file timeout adaptively.

    Strategy: allocate remaining wall time across remaining files,
    multiplied by the worker count (parallelism), with a floor and cap.
    Early on, uses the estimated_hours budget (which oversubscribes)
    so files get generous timeouts. As files complete, it transitions
    to using actual remaining wall time.
    """

    def __init__(self, total_files: int, wall_seconds: float,
                 estimated_seconds: float, workers: int):
        self.total_files = total_files
        self.wall_seconds = wall_seconds
        self.estimated_seconds = estimated_seconds
        self.workers = workers
        self.start_time = time.monotonic()
        self.completed = 0
        self.lock = Lock()

        # Initial timeout: spread estimated budget across files
        self._timeout = min(
            MAX_TIMEOUT,
            max(MIN_TIMEOUT, estimated_seconds / total_files * workers)
        )

    @property
    def timeout(self) -> float:
        return self._timeout

    def record_completion(self):
        with self.lock:
            self.completed += 1
            remaining_files = self.total_files - self.completed
            if remaining_files <= 0:
                return

            elapsed = time.monotonic() - self.start_time
            remaining_wall = max(60, self.wall_seconds - elapsed)

            # Budget-based timeout: spread remaining time across remaining files
            budget_timeout = remaining_wall / remaining_files * self.workers

            # Blend: use the more generous of estimated vs wall-clock budget,
            # but as we approach the wall-clock deadline, shift to wall-clock.
            elapsed_frac = elapsed / self.wall_seconds  # 0→1 over wall budget
            wall_weight = max(0.0, min(1.0, (elapsed_frac - 0.5) * 4))  # 0 until 50% elapsed, then ramps to 1 at 75%
            est_remaining = max(0, self.estimated_seconds - elapsed)
            est_timeout = est_remaining / remaining_files * self.workers if est_remaining > 0 else budget_timeout

            blended = max(est_timeout, budget_timeout) * (1 - wall_weight) + budget_timeout * wall_weight

            self._timeout = min(MAX_TIMEOUT, max(MIN_TIMEOUT, blended))

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.start_time

    @property
    def remaining_wall(self) -> float:
        return max(0, self.wall_seconds - self.elapsed)

    @property
    def is_time_up(self) -> bool:
        return self.elapsed >= self.wall_seconds


# ── TUI ───────────────────────────────────────────────────────────────────────

class TUI:
    """Simple terminal UI using ANSI escape codes (no curses dependency)."""

    CLEAR_LINE = "\033[2K"
    MOVE_UP = "\033[A"
    HIDE_CURSOR = "\033[?25l"
    SHOW_CURSOR = "\033[?25h"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    def __init__(self, total_files: int):
        self.total = total_files
        self.completed = 0
        self.outcomes = defaultdict(int)
        self.timeout_val = 0.0
        self.elapsed = 0.0
        self.remaining_wall = 0.0
        self.lock = Lock()
        self._lines_printed = 0
        self._last_failures: list[str] = []
        self._max_recent = 8
        self._started = False

    def start(self):
        sys.stderr.write(self.HIDE_CURSOR)
        sys.stderr.flush()
        self._started = True

    def stop(self):
        if self._started:
            sys.stderr.write(self.SHOW_CURSOR)
            sys.stderr.flush()

    def update(self, result: Result, tm: TimeoutManager):
        with self.lock:
            self.completed += 1
            self.outcomes[result.outcome] += 1
            self.timeout_val = tm.timeout
            self.elapsed = tm.elapsed
            self.remaining_wall = tm.remaining_wall

            if result.outcome not in ("ok", "timeout"):
                short = result.path.split("non-incremental/")[-1] if "non-incremental/" in result.path else os.path.basename(result.path)
                tag = result.outcome.upper()
                if result.signal_num:
                    tag += f"(sig {result.signal_num})"
                line = f"  {tag}: {short}"
                self._last_failures.append(line)
                if len(self._last_failures) > self._max_recent:
                    self._last_failures = self._last_failures[-self._max_recent:]

            self._render()

    def _render(self):
        # Move up to overwrite previous output
        if self._lines_printed > 0:
            sys.stderr.write(f"\033[{self._lines_printed}A")

        lines = []
        lines.append("")
        lines.append(f"{self.BOLD}{'═' * 62}{self.RESET}")
        lines.append(f"{self.BOLD}  STP FP Benchmark Runner{self.RESET}")
        lines.append(f"{'═' * 62}")

        # Progress bar
        pct = self.completed / self.total if self.total else 0
        bar_w = 40
        filled = int(bar_w * pct)
        bar = "█" * filled + "░" * (bar_w - filled)
        lines.append(f"  [{bar}] {pct*100:5.1f}%")
        lines.append("")

        # Stats
        remaining = self.total - self.completed
        ok = self.outcomes.get("ok", 0)
        wrong = self.outcomes.get("wrong_answer", 0)
        crash = self.outcomes.get("crash", 0)
        err = self.outcomes.get("error", 0)
        tout = self.outcomes.get("timeout", 0)
        failures = wrong + crash + err

        lines.append(f"  {self.CYAN}Completed:{self.RESET}  {self.completed:>7,}  / {self.total:,}")
        lines.append(f"  {self.CYAN}Remaining:{self.RESET}  {remaining:>7,}")
        lines.append("")
        lines.append(f"  {self.GREEN}OK:{self.RESET}         {ok:>7,}    "
                      f"{self.RED}Failures:{self.RESET}  {failures:>5,}  "
                      f"{self.YELLOW}Timeouts:{self.RESET} {tout:>5,}")

        if failures > 0:
            detail_parts = []
            if wrong: detail_parts.append(f"wrong_answer={wrong}")
            if crash: detail_parts.append(f"crash={crash}")
            if err: detail_parts.append(f"error={err}")
            lines.append(f"  {self.DIM}({', '.join(detail_parts)}){self.RESET}")

        lines.append("")
        lines.append(f"  {self.CYAN}Timeout:{self.RESET}    {self.timeout_val:>7.1f}s   "
                      f"{self.CYAN}Elapsed:{self.RESET} {self._fmt_time(self.elapsed)}   "
                      f"{self.CYAN}Left:{self.RESET} {self._fmt_time(self.remaining_wall)}")

        # ETA
        if self.completed > 0:
            rate = self.completed / self.elapsed if self.elapsed > 0 else 0
            eta = remaining / rate if rate > 0 else 0
            lines.append(f"  {self.CYAN}Rate:{self.RESET}      {rate:>7.1f}/s   "
                          f"{self.CYAN}ETA:{self.RESET}     {self._fmt_time(eta)}")

        if self._last_failures:
            lines.append("")
            lines.append(f"  {self.RED}{self.BOLD}Recent failures:{self.RESET}")
            for f in self._last_failures:
                lines.append(f"{self.RED}{f}{self.RESET}")

        lines.append(f"{'─' * 62}")

        output = "\n".join(self.CLEAR_LINE + l for l in lines)
        sys.stderr.write(output + "\n")
        sys.stderr.flush()
        self._lines_printed = len(lines)

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        if h > 0:
            return f"{h}h{m:02d}m{s:02d}s"
        elif m > 0:
            return f"{m}m{s:02d}s"
        else:
            return f"{s}s"


# ── Main ──────────────────────────────────────────────────────────────────────

def collect_files(directory: Path) -> list[str]:
    files = []
    for root, _, names in os.walk(directory):
        for name in names:
            if name.endswith(".smt2"):
                files.append(os.path.join(root, name))
    return files


class FailureLog:
    """Writes failures incrementally to a CSV as they arrive."""

    HEADER = ["path", "outcome", "exit_code", "signal", "elapsed",
              "expected", "actual", "stderr_head"]

    def __init__(self, path: Path):
        self.path = path
        self.lock = Lock()
        self._started = False

    def _ensure_header(self):
        if not self._started:
            with open(self.path, "w", newline="") as f:
                csv.writer(f).writerow(self.HEADER)
            self._started = True

    def record(self, r: Result):
        if r.outcome in ("ok", "timeout"):
            return
        with self.lock:
            self._ensure_header()
            with open(self.path, "a", newline="") as f:
                csv.writer(f).writerow([
                    r.path, r.outcome, r.exit_code, r.signal_num,
                    f"{r.elapsed:.3f}", r.expected, r.actual, r.stderr_head
                ])


def write_summary(results: list[Result], tm: TimeoutManager, path: Path):
    summary = {
        "total_files": tm.total_files,
        "completed": len(results),
        "elapsed_seconds": round(tm.elapsed, 1),
        "outcomes": defaultdict(int),
        "failures": [],
    }
    for r in results:
        summary["outcomes"][r.outcome] += 1
        if r.outcome not in ("ok", "timeout"):
            summary["failures"].append({
                "path": r.path,
                "outcome": r.outcome,
                "exit_code": r.exit_code,
                "signal": r.signal_num,
                "expected": r.expected,
                "actual": r.actual,
                "stderr": r.stderr_head,
            })
    summary["outcomes"] = dict(summary["outcomes"])

    with open(path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Summary written to: {path}")


def main():
    parser = argparse.ArgumentParser(description="Run .smt2 files against STP")
    parser.add_argument("--stp", type=Path, default=DEFAULT_STP,
                        help="Path to stp_simple binary")
    parser.add_argument("--dir", type=Path, nargs="*", default=DEFAULT_DIRS,
                        help="Directories containing .smt2 files")
    parser.add_argument("--file-list", type=Path, default=None,
                        help="Text file with one .smt2 path per line (overrides --dir)")
    parser.add_argument("--stp-args", nargs=argparse.REMAINDER, default=[],
                        help="Extra arguments to pass to stp_simple (put after --)")
    parser.add_argument("--wall-hours", type=float, default=DEFAULT_WALL_HOURS,
                        help="Wall-clock budget in hours")
    parser.add_argument("--estimated-hours", type=float, default=DEFAULT_ESTIMATED_HOURS,
                        help="Estimated total time (for initial timeout calc)")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help="Number of parallel workers")
    parser.add_argument("--output", type=Path, default=Path("stp_failures.csv"),
                        help="Output CSV for failures")
    parser.add_argument("--summary", type=Path, default=Path("stp_summary.json"),
                        help="Output JSON summary")
    args = parser.parse_args()

    stp_bin = str(args.stp.resolve())
    if not os.path.isfile(stp_bin):
        print(f"Error: STP binary not found at {stp_bin}", file=sys.stderr)
        sys.exit(1)

    files = []
    if args.file_list:
        print(f"Reading file list from {args.file_list} ...")
        with open(args.file_list) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    files.append(line)
    else:
        for d in args.dir:
            print(f"Collecting .smt2 files from {d} ...")
            files.extend(collect_files(d))
    if not files:
        print("No .smt2 files found.", file=sys.stderr)
        sys.exit(1)
    # Sort by file size (smallest first) to get quick wins early
    files.sort(key=lambda p: os.path.getsize(p))

    print(f"Found {len(files):,} files. Using {args.workers} workers.")
    print(f"Wall budget: {args.wall_hours}h, estimated: {args.estimated_hours}h")

    wall_seconds = args.wall_hours * 3600
    estimated_seconds = args.estimated_hours * 3600
    tm = TimeoutManager(len(files), wall_seconds, estimated_seconds, args.workers)
    tui = TUI(len(files))

    extra_args = tuple(args.stp_args)
    if extra_args:
        print(f"Extra STP args: {' '.join(extra_args)}")
    print(f"Initial timeout: {tm.timeout:.1f}s")
    print(f"Starting run...\n")

    tui.start()
    results: list[Result] = []
    failure_log = FailureLog(args.output)

    try:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            # Submit in batches to allow timeout adaptation
            BATCH = args.workers * 4
            file_iter = iter(files)
            pending = {}
            done = False

            # Seed initial batch
            for _ in range(min(BATCH, len(files))):
                try:
                    path = next(file_iter)
                    fut = executor.submit(run_one, stp_bin, path, tm.timeout, extra_args)
                    pending[fut] = path
                except StopIteration:
                    done = True
                    break

            while pending:
                if tm.is_time_up:
                    # Cancel remaining work
                    for fut in pending:
                        fut.cancel()
                    break

                # Wait for completions
                completed_futs = []
                try:
                    for fut in as_completed(pending, timeout=1.0):
                        completed_futs.append(fut)
                        # Don't block too long, let us check time and submit more
                        if len(completed_futs) >= args.workers:
                            break
                except TimeoutError:
                    pass  # no futures completed in 1s, that's fine

                for fut in completed_futs:
                    try:
                        result = fut.result()
                        results.append(result)
                        failure_log.record(result)
                        tm.record_completion()
                        tui.update(result, tm)
                    except Exception:
                        pass
                    del pending[fut]

                    # Submit replacement work
                    if not done:
                        try:
                            path = next(file_iter)
                            new_fut = executor.submit(run_one, stp_bin, path, tm.timeout, extra_args)
                            pending[new_fut] = path
                        except StopIteration:
                            done = True

    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
    finally:
        tui.stop()

    # Final report
    outcomes = defaultdict(int)
    for r in results:
        outcomes[r.outcome] += 1

    failures = outcomes.get("wrong_answer", 0) + outcomes.get("crash", 0) + outcomes.get("error", 0)

    print(f"\n{'=' * 62}")
    print(f"  FINAL RESULTS")
    print(f"{'=' * 62}")
    print(f"  Completed: {len(results):,} / {len(files):,}")
    print(f"  Elapsed:   {tui._fmt_time(tm.elapsed)}")
    print()
    print(f"  OK:            {outcomes.get('ok', 0):>7,}")
    print(f"  Wrong answer:  {outcomes.get('wrong_answer', 0):>7,}")
    print(f"  Crash:         {outcomes.get('crash', 0):>7,}")
    print(f"  Error:         {outcomes.get('error', 0):>7,}")
    print(f"  Timeout:       {outcomes.get('timeout', 0):>7,}")
    print(f"  ─────────────────────────")
    print(f"  FAILURES:      {failures:>7,}")
    print(f"{'=' * 62}")

    if failure_log._started:
        print(f"\nFailures log written to: {args.output}")
    write_summary(results, tm, args.summary)


if __name__ == "__main__":
    main()
