"""Solver execution and parallel dispatch."""

import subprocess
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from .results import Result


def run_one(solver_name, solver_bin, extra_args, path, run, timeout):
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
    except Exception:
        elapsed = time.monotonic() - t0
        return Result(path, solver_name, run, elapsed, "error", -1, 0, timeout)


def run_pool(tasks, workers, timeout, on_result, wall_seconds=None):
    """Run solver tasks in parallel using a process pool.

    Args:
        tasks: iterable of (solver_name, binary, extra_args, path, run_idx) tuples
        workers: number of parallel workers
        timeout: per-task timeout in seconds
        on_result: callback(Result) called for each completed task
        wall_seconds: optional wall-clock budget; None means unlimited

    Returns:
        Number of tasks completed.
    """
    tasks = list(tasks)
    total = len(tasks)
    if total == 0:
        return 0

    start_time = time.monotonic()
    completed = 0

    with ProcessPoolExecutor(max_workers=workers) as executor:
        BATCH = workers * 4
        task_iter = iter(tasks)
        pending = {}
        exhausted = False

        # Seed
        for _ in range(min(BATCH, total)):
            try:
                name, binary, extra_args, path, run_idx = next(task_iter)
                fut = executor.submit(run_one, name, binary, extra_args, path, run_idx, timeout)
                pending[fut] = True
            except StopIteration:
                exhausted = True
                break

        while pending:
            if wall_seconds is not None:
                elapsed = time.monotonic() - start_time
                if elapsed >= wall_seconds:
                    for fut in pending:
                        fut.cancel()
                    break

            batch = []
            try:
                for fut in as_completed(pending, timeout=1.0):
                    batch.append(fut)
                    if len(batch) >= workers:
                        break
            except TimeoutError:
                pass

            for fut in batch:
                try:
                    result = fut.result()
                    on_result(result)
                    completed += 1
                except Exception:
                    pass
                del pending[fut]

                if not exhausted:
                    try:
                        name, binary, extra_args, path, run_idx = next(task_iter)
                        new_fut = executor.submit(run_one, name, binary, extra_args, path, run_idx, timeout)
                        pending[new_fut] = True
                    except StopIteration:
                        exhausted = True

    return completed
