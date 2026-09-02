"""Live terminal progress display for a running comparison."""

import heapq
import statistics
import sys
import time
from threading import Lock

from .fmt import BOLD, RED, GREEN, YELLOW, CYAN, MAGENTA, RST, CL, HIDE, SHOW, fmt_duration
from .paths import shorten
from .results import CONCLUSIVE_ANSWERS, majority_answer

COUNTED_ANSWERS = ("sat", "unsat", "unknown", "timeout", "error", "crash")


class RunningMedian:
    """Median of a stream of values, kept with a pair of heaps.

    The whole point is not to retain every elapsed time for the length of a
    24-hour run just to print one number.
    """

    def __init__(self):
        self._lower = []   # max-heap (negated)
        self._upper = []   # min-heap

    def add(self, value):
        if not self._lower or value <= -self._lower[0]:
            heapq.heappush(self._lower, -value)
        else:
            heapq.heappush(self._upper, value)

        if len(self._lower) > len(self._upper) + 1:
            heapq.heappush(self._upper, -heapq.heappop(self._lower))
        elif len(self._upper) > len(self._lower):
            heapq.heappush(self._lower, -heapq.heappop(self._upper))

    def value(self):
        if not self._lower:
            return 0.0
        if len(self._lower) == len(self._upper):
            return (-self._lower[0] + self._upper[0]) / 2.0
        return -self._lower[0]


class TUI:
    """Progress, per-solver counters, and a live scoreboard per alternative.

    Every solver after the first is scored against the first, which keeps the
    display meaningful whether the run compares two builds or seven option
    sets.
    """

    WIN_RATIO = 0.5       # alt is 2x+ faster -> win
    LOSE_RATIO = 5.0      # alt is 5x+ slower -> loss
    MASSIVE_RATIO = 20.0  # alt is 20x+ slower -> massive loss
    MAX_RECENT = 5

    def __init__(self, title, total_tasks, total_files, solver_names, runs):
        self.title = title
        self.total_tasks = total_tasks
        self.total_files = total_files
        self.solver_names = list(solver_names)
        self.runs = runs
        self.completed = 0
        self.start_time = time.monotonic()
        self.lock = Lock()
        self._lines = 0

        self._base_name = self.solver_names[0]
        self._alt_names = self.solver_names[1:]
        self._multi_alt = len(self._alt_names) > 1

        self.per_solver = {
            s: {**{a: 0 for a in COUNTED_ANSWERS}, "done": 0, "other": 0,
                "total_time": 0.0, "median": RunningMedian()}
            for s in self.solver_names
        }
        self._score = {
            alt: {"wins": 0, "losses": 0, "massive": 0, "ties": 0,
                  "unique_solves": 0, "unique_fails": 0}
            for alt in self._alt_names
        }

        # Results are held only until every run of every solver on that file
        # has landed, then discarded.
        self._file_results: dict[str, dict[str, list]] = {}
        self._file_task_count: dict[str, int] = {}
        self._expected_per_file = len(self.solver_names) * runs
        self._files_compared = 0

        self._recent_wins: list[str] = []
        self._recent_losses: list[str] = []

    def start(self):
        sys.stderr.write(HIDE)
        sys.stderr.flush()

    def stop(self):
        sys.stderr.write(SHOW)
        sys.stderr.flush()

    # ── Classification ────────────────────────────────────────────────────

    def _note(self, bucket, line):
        bucket.append(line)
        del bucket[:-self.MAX_RECENT]

    def _classify_file(self, file_results):
        """Score each alt against the base once every run on this file is in."""
        self._files_compared += 1

        base_runs = file_results.get(self._base_name, [])
        if not base_runs:
            return
        base_ans = majority_answer([r.answer for r in base_runs])
        base_ok = base_ans in CONCLUSIVE_ANSWERS
        base_t = statistics.median(r.elapsed for r in base_runs)
        short = shorten(base_runs[0].path)

        for alt_name in self._alt_names:
            alt_runs = file_results.get(alt_name, [])
            if not alt_runs:
                continue
            tag = f"[{alt_name}] " if self._multi_alt else ""
            score = self._score[alt_name]

            alt_ans = majority_answer([r.answer for r in alt_runs])
            alt_ok = alt_ans in CONCLUSIVE_ANSWERS
            alt_t = statistics.median(r.elapsed for r in alt_runs)

            if alt_ok and base_ans == "timeout":
                score["unique_solves"] += 1
                self._note(self._recent_wins,
                           f"  {tag}UNIQUE SOLVE ({alt_t:.2f}s, "
                           f"{self._base_name} timed out): {short}")
                continue

            if alt_ans == "timeout" and base_ok:
                score["unique_fails"] += 1
                self._note(self._recent_losses,
                           f"  {tag}TIMEOUT ({self._base_name} did it in "
                           f"{base_t:.2f}s): {short}")
                continue

            # Anything else is only comparable if both actually solved it.
            if not alt_ok or not base_ok:
                continue

            # Avoid division by zero on very fast solves.
            if base_t < 0.001 and alt_t < 0.001:
                score["ties"] += 1
                continue
            ratio = alt_t / max(base_t, 0.001)

            if ratio >= self.LOSE_RATIO:
                score["losses"] += 1
                if ratio >= self.MASSIVE_RATIO:
                    score["massive"] += 1
                self._note(self._recent_losses,
                           f"  {tag}{alt_t:.2f}s vs {self._base_name} "
                           f"{base_t:.2f}s ({ratio:.0f}x slower): {short}")
            elif ratio <= self.WIN_RATIO:
                score["wins"] += 1
                inv = 1.0 / ratio if ratio > 0 else 999
                self._note(self._recent_wins,
                           f"  {tag}{alt_t:.2f}s vs {self._base_name} "
                           f"{base_t:.2f}s ({inv:.0f}x faster): {short}")
            else:
                score["ties"] += 1

    def update(self, r):
        with self.lock:
            self.completed += 1
            s = self.per_solver[r.solver]
            s["done"] += 1
            s["total_time"] += r.elapsed
            s["median"].add(r.elapsed)
            if r.answer in COUNTED_ANSWERS:
                s[r.answer] += 1
            else:
                s["other"] += 1

            per_file = self._file_results.setdefault(r.path, {})
            per_file.setdefault(r.solver, []).append(r)
            self._file_task_count[r.path] = self._file_task_count.get(r.path, 0) + 1

            if self._file_task_count[r.path] == self._expected_per_file:
                self._classify_file(self._file_results.pop(r.path))
                del self._file_task_count[r.path]

            self._render()

    # ── Rendering ─────────────────────────────────────────────────────────

    def _render(self):
        if self._lines > 0:
            sys.stderr.write(f"\033[{self._lines}A")

        elapsed = time.monotonic() - self.start_time
        name_w = max(12, min(28, max(len(s) for s in self.solver_names)))
        width = max(95, name_w + 82)
        rule = "─" * (width - 4)

        lines = ["", f"{BOLD}{'═' * width}{RST}",
                 f"{BOLD}  {self.title}: {len(self.solver_names)} solvers "
                 f"({self.runs} runs/file){RST}",
                 "═" * width]

        pct = self.completed / self.total_tasks if self.total_tasks else 0
        bar_w = min(50, width - 28)
        filled = int(bar_w * pct)
        lines.append(f"  [{'█' * filled}{'░' * (bar_w - filled)}] {pct*100:5.1f}%")
        lines.append("")
        lines.append(f"  {CYAN}Tasks:{RST}    {self.completed:>8,} / {self.total_tasks:,}")
        lines.append(f"  {CYAN}Files:{RST}    {self._files_compared:>8,} / "
                     f"{self.total_files:,}  fully compared (median of {self.runs} runs)")
        lines.append("")

        hdr = (f"  {'Solver':<{name_w}} {'Runs':>7} {'sat':>7} {'unsat':>7} "
               f"{'unk':>6} {'TO':>6} {'err':>5} {'crash':>5} {'oth':>5} "
               f"{'mean(s)':>8} {'med(s)':>8}")
        lines.append(f"{BOLD}{hdr}{RST}")
        lines.append(f"  {rule}")
        for name in self.solver_names:
            s = self.per_solver[name]
            mean = s["total_time"] / s["done"] if s["done"] else 0.0
            lines.append(
                f"  {name:<{name_w}} {s['done']:>7,} {s['sat']:>7,} "
                f"{s['unsat']:>7,} {s['unknown']:>6,} {s['timeout']:>6,} "
                f"{s['error']:>5,} {s['crash']:>5,} {s['other']:>5,} "
                f"{mean:>8.2f} {s['median'].value():>8.2f}"
            )
        lines.append("")

        lines.append(f"{BOLD}  Scoreboard vs {self._base_name}{RST}  "
                     f"({self._files_compared:,} files compared)")
        shdr = (f"  {'Solver':<{name_w}} {'wins':>7} {'ties':>7} {'losses':>7} "
                f"{'massive':>8} {'uniq solve':>11} {'uniq TO':>8}")
        lines.append(f"{BOLD}{shdr}{RST}")
        lines.append(f"  {rule}")
        for alt in self._alt_names:
            c = self._score[alt]
            lines.append(
                f"  {alt:<{name_w}} {GREEN}{c['wins']:>7,}{RST} "
                f"{YELLOW}{c['ties']:>7,}{RST} {RED}{c['losses']:>7,}{RST} "
                f"{MAGENTA}{c['massive']:>8,}{RST} "
                f"{GREEN}{c['unique_solves']:>11,}{RST} "
                f"{RED}{c['unique_fails']:>8,}{RST}"
            )
        lines.append("")

        if self._recent_wins:
            lines.append(f"  {GREEN}{BOLD}Recent wins (2x+ faster than "
                         f"{self._base_name}):{RST}")
            lines.extend(f"{GREEN}{w}{RST}" for w in self._recent_wins)
            lines.append("")

        if self._recent_losses:
            lines.append(f"  {RED}{BOLD}Recent losses (5x+ slower than "
                         f"{self._base_name}):{RST}")
            lines.extend(f"{RED}{l}{RST}" for l in self._recent_losses)
            lines.append("")

        rate = self.completed / elapsed if elapsed > 0 else 0
        eta = (self.total_tasks - self.completed) / rate if rate > 0 else 0
        lines.append(f"  {CYAN}Elapsed:{RST} {fmt_duration(elapsed)}   "
                     f"{CYAN}Rate:{RST} {rate:.1f} tasks/s   "
                     f"{CYAN}ETA:{RST} {fmt_duration(eta)}")
        lines.append("─" * width)

        sys.stderr.write("\n".join(CL + l for l in lines) + "\n")
        sys.stderr.flush()
        self._lines = len(lines)
