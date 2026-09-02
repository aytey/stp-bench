"""Run a configured comparison: main pass, revalidation, and final report."""

import csv
import datetime
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

from .fmt import BOLD, RED, GREEN, RST, fmt_duration
from .paths import collect_smt2_files, load_file_list, shorten
from .results import (
    CONCLUSIVE_ANSWERS, ResultLog, collect_answer_disagreements, load_combined,
    load_medians, majority_answer, pair_files, score_table,
    write_answer_disagreements, write_manifest,
)
from .runner import run_pool
from .tui import TUI

ANSWER_DISAGREEMENT_PREVIEW = 20


def get_git_hash(binary_path):
    """Short git hash of the repository containing a solver binary."""
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


def default_output(config, git_hashes):
    """Auto-generated CSV name: <prefix>_<hash>_vs_<hash>.csv.

    Hashes are deduplicated: several configurations of one build share a
    binary, and repeating its hash once per configuration made for filenames
    like `..._vs_97717546_vs_97717546_vs_97717546.csv`.
    """
    unique = list(dict.fromkeys(git_hashes[s.name] for s in config.solvers))
    return Path(f"{config.output_prefix}_{'_vs_'.join(unique)}.csv")


def build_manifest(config, solver_list, git_hashes):
    """What produced this CSV: the config, the binaries, and their arguments.

    The CSV records solver names only, in completion order, so without this
    there is nothing tying `stp-cegar-w16` to the arguments it stood for.
    """
    return {
        "config": config.name,
        "source": str(config.source.resolve()),
        "defaults": (str(Path(config.defaults_source).resolve())
                     if config.defaults_source else None),
        "override": (str(Path(config.override_source).resolve())
                     if config.override_source else None),
        "started": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "timeout": config.timeout,
        "runs": config.runs,
        "incremental": config.incremental,
        "virtual_timeouts": config.virtual_timeouts,
        "solvers": [
            {"name": name, "binary": binary, "args": args,
             "git_hash": git_hashes[name]}
            for name, binary, args in solver_list
        ],
    }


def collect_inputs(config):
    """The .smt2 files to run, smallest first."""
    if config.file_list:
        print(f"Reading file list from {config.file_list} ...")
        return load_file_list(config.file_list)

    files = []
    for d in config.dirs:
        print(f"Collecting .smt2 files from {d} ...")
        files.extend(collect_smt2_files([d]))
    files.sort(key=lambda p: os.path.getsize(p))
    return files


def build_tasks(solver_list, files, runs):
    """One task per (file, run, solver), interleaved so solvers stay in step."""
    return [(name, binary, args, path, run_idx)
            for path in files
            for run_idx in range(runs)
            for name, binary, args in solver_list]


def read_per_file(csv_path):
    """Group a results CSV into path -> solver -> (elapsed list, answer list)."""
    runs: dict[str, dict[str, list[float]]] = {}
    answers: dict[str, dict[str, list[str]]] = {}
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            p, s = row["path"], row["solver"]
            runs.setdefault(p, {}).setdefault(s, []).append(float(row["elapsed"]))
            answers.setdefault(p, {}).setdefault(s, []).append(row["answer"])
    return runs, answers


def has_answer_disagreement(answers_by_solver):
    """True if two solvers reached opposite conclusive answers on this file."""
    conclusive = {majority_answer(a) for a in answers_by_solver.values() if a}
    return len({a for a in conclusive if a in CONCLUSIVE_ANSWERS}) > 1


def flags_revalidation(per_file_runs, per_file_answers, path, base_name, alt_name, ratio):
    """Does the base disagree with this one alt enough to be worth rerunning?"""
    m_times = per_file_runs[path].get(base_name, [])
    q_times = per_file_runs[path].get(alt_name, [])
    if not m_times or not q_times:
        return False

    m_ans = majority_answer(per_file_answers[path].get(base_name, []))
    q_ans = majority_answer(per_file_answers[path].get(alt_name, []))

    # `unknown` is deliberately not comparable here, in either role. A solver
    # that does not support a query answers `unknown` in milliseconds because
    # it never starts solving; timed against a solver that does solve it, that
    # is a >= 3x gap on almost every such file. Rerunning at four times the
    # timeout re-measures the same not-attempted-versus-attempted difference,
    # so it is excluded rather than revalidated. A genuine timeout split still
    # flags below.
    m_ok = m_ans in CONCLUSIVE_ANSWERS
    q_ok = q_ans in CONCLUSIVE_ANSWERS

    if m_ans == "timeout" and q_ans == "timeout":
        return True
    if m_ok and q_ans == "timeout":
        return True
    if q_ok and m_ans == "timeout":
        return True

    if m_ok and q_ok:
        r = statistics.median(q_times) / max(statistics.median(m_times), 0.001)
        return r >= ratio or r <= 1.0 / ratio
    return False


def select_revalidation(config, csv_path):
    """Files worth rerunning at a longer timeout, smallest first.

    Every alt gets a say. Flagging on the first one alone would leave a
    configuration that timed out where the others answered -- exactly the case
    the longer timeout exists to settle -- unrevalidated, and the file is rerun
    for all solvers anyway, so one alt asking is enough.
    """
    per_file_runs, per_file_answers = read_per_file(csv_path)
    base_name = config.base.name
    alt_names = [s.name for s in config.alts]

    flagged = [
        path for path in per_file_runs
        if has_answer_disagreement(per_file_answers[path])
        or any(flags_revalidation(per_file_runs, per_file_answers, path,
                                  base_name, alt, config.revalidate_ratio)
               for alt in alt_names)
    ]
    flagged.sort(key=lambda p: os.path.getsize(p))
    return flagged


def run_pass(config, tasks, output, title, total_files, timeout, wall_seconds=None):
    """Run one pass of tasks, logging to CSV and driving the live display."""
    log = ResultLog(output)
    tui = TUI(title, len(tasks), total_files, config.solver_names, config.runs)
    tui.start()

    def on_result(result):
        log.record(result)
        tui.update(result)

    try:
        return run_pool(tasks, config.workers, timeout, on_result,
                        wall_seconds=wall_seconds,
                        incremental=config.incremental)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
        return tui.completed
    finally:
        tui.stop()


def report_answer_disagreements(final_data, config, output):
    """Print, and write out, any sat/unsat split between configurations."""
    disagreements = collect_answer_disagreements(final_data, config.solver_names)
    if not disagreements:
        print(f"{GREEN}  No sat/unsat answer disagreements across solvers.{RST}\n")
        return

    path = output.parent / (output.stem + "_answer_disagreements" + output.suffix)
    write_answer_disagreements(path, disagreements, config.solver_names)

    shown = min(ANSWER_DISAGREEMENT_PREVIEW, len(disagreements))
    print(f"{RED}{BOLD}  SAT/UNSAT ANSWER DISAGREEMENTS: {len(disagreements):,} files{RST}")
    print(f"  Full list: {path}")
    print(f"  Showing first {shown:,} (smallest files first):")
    for input_path, answers in disagreements[:ANSWER_DISAGREEMENT_PREVIEW]:
        print(f"  {shorten(input_path)}")
        print("    " + "  ".join(f"{n}={answers[n]}" for n in config.solver_names))
    if len(disagreements) > shown:
        print(f"  ... {len(disagreements) - shown:,} more in {path}")
    print()


def run_experiment(config, output=None, force=False):
    """Run the whole comparison described by `config`. Returns the exit code."""
    solver_list = []
    git_hashes = {}
    layers = [str(config.source)]
    if config.defaults_source:
        layers.insert(0, str(config.defaults_source))
    if config.override_source:
        layers.append(str(config.override_source))
    print(f"{BOLD}{config.name}{RST}  (config: {' -> '.join(layers)})")
    for solver in config.solvers:
        p = str(solver.binary.resolve())
        if not os.path.isfile(p):
            print(f"Warning: {solver.name} binary not found at {p} (not yet built?)",
                  file=sys.stderr)
        git_hashes[solver.name] = get_git_hash(p)
        solver_list.append((solver.name, p, solver.args))
        args = " ".join(solver.args)
        print(f"  {solver.name}: {p}  (git {git_hashes[solver.name]})"
              + (f"\n      args: {args}" if args else ""))

    output = Path(output) if output else default_output(config, git_hashes)
    # The output name is derived from git hashes alone, so a rerun of the same
    # binaries -- or a `--solvers` subset of them -- lands on the name an
    # earlier run already used. ResultLog opens it for writing, so without this
    # a stray rerun silently truncates a finished 24-hour run.
    if output.exists() and not force:
        print(f"Refusing to overwrite existing results: {output}\n"
              f"  Pass --output to write elsewhere, or --force to overwrite.",
              file=sys.stderr)
        return 1

    files = collect_inputs(config)
    if not files:
        print("No .smt2 files found.", file=sys.stderr)
        return 1

    tasks = build_tasks(solver_list, files, config.runs)
    mode = "incremental (stdin)" if config.incremental else "batch (file arg)"
    print(f"Found {len(files):,} files x {len(solver_list)} solvers x "
          f"{config.runs} runs = {len(tasks):,} tasks")
    print(f"Workers: {config.workers}, timeout: {config.timeout:.0f}s, "
          f"wall budget: {config.wall_hours}h, mode: {mode}")
    manifest = write_manifest(output, build_manifest(config, solver_list, git_hashes))
    print(f"Output: {output}")
    print(f"Manifest: {manifest}")
    print("Starting run...\n")

    start_time = time.monotonic()
    completed = run_pass(config, tasks, output, config.name, len(files),
                         config.timeout, wall_seconds=config.wall_hours * 3600)

    # ── Revalidation pass ────────────────────────────────────────────────
    reval_output = None
    revalidate_files = select_revalidation(config, output) if config.revalidate else []
    reval_timeout = config.resolved_revalidate_timeout()

    if revalidate_files:
        reval_tasks = build_tasks(solver_list, revalidate_files, config.runs)
        reval_output = output.parent / (output.stem + "_revalidation" + output.suffix)

        print(f"\n{'=' * 72}")
        print(f"  REVALIDATION: {len(revalidate_files):,} files flagged "
              f"(answer disagreement, timeout, or >= {config.revalidate_ratio:.0f}x either way)")
        print(f"  {len(reval_tasks):,} tasks, timeout {reval_timeout:.0f}s, "
              f"{config.runs} runs/file")
        print(f"  Output: {reval_output}")
        print(f"{'=' * 72}\n")

        reval_completed = run_pass(config, reval_tasks, reval_output,
                                   f"{config.name} [revalidation]",
                                   len(revalidate_files), reval_timeout)
        print(f"\n  Revalidation: {reval_completed:,} / {len(reval_tasks):,} tasks completed")
    elif config.revalidate:
        print(f"\n  No files had answer disagreements, timeout splits, or "
              f">= {config.revalidate_ratio:.0f}x timing gaps — revalidation skipped.")
    else:
        print("\n  Revalidation disabled in config.")

    # ── Final summary ────────────────────────────────────────────────────
    elapsed = time.monotonic() - start_time
    print(f"\n{'=' * 72}")
    print("  COMPARISON COMPLETE")
    print(f"{'=' * 72}")
    print(f"  Tasks completed: {completed:,} / {len(tasks):,}")
    if revalidate_files:
        print(f"  Revalidated:     {len(revalidate_files):,} files")
    print(f"  Elapsed: {fmt_duration(elapsed)}")
    print(f"  Results: {output}")
    if reval_output:
        print(f"  Revalidation: {reval_output}")
    print(f"{'=' * 72}\n")

    if reval_output:
        final_data = load_combined(str(output), str(reval_output))
    else:
        final_data = load_medians(str(output))

    report_answer_disagreements(final_data, config, output)

    for alt in config.alts:
        paired = pair_files(final_data, config.base.name, alt.name)
        for vto in config.virtual_timeouts:
            score_table(paired, config.base.name, alt.name, vto)

    return 0
