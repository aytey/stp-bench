"""Solver execution and parallel dispatch."""

import select
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from .results import Result


def parse_commands(filepath):
    """Parse an SMT-LIB2 file into top-level S-expression commands.

    Handles string literals, |quoted symbols|, and ; line comments.
    Returns a list of (command_string, is_check_sat) tuples.
    """
    with open(filepath, "r", errors="replace") as f:
        content = f.read()

    commands = []
    i = 0
    n = len(content)

    while i < n:
        # Skip whitespace
        if content[i] in (" ", "\t", "\n", "\r"):
            i += 1
            continue
        # Skip line comments
        if content[i] == ";":
            while i < n and content[i] != "\n":
                i += 1
            continue
        # Parse a top-level S-expression
        if content[i] == "(":
            depth = 0
            start = i
            while i < n:
                c = content[i]
                if c == ";":
                    while i < n and content[i] != "\n":
                        i += 1
                    continue
                if c == '"':
                    i += 1
                    while i < n:
                        if content[i] == '"':
                            i += 1
                            if i < n and content[i] == '"':
                                i += 1  # escaped quote ""
                                continue
                            break
                        i += 1
                    continue
                if c == "|":
                    i += 1
                    while i < n and content[i] != "|":
                        i += 1
                    if i < n:
                        i += 1
                    continue
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        cmd = content[start:i].strip()
                        # Detect check-sat variants
                        inner = cmd[1:].lstrip()
                        is_cs = inner.startswith("check-sat")
                        commands.append((cmd, is_cs))
                        break
                i += 1
        else:
            i += 1

    return commands


def _read_line_timeout(proc, deadline):
    """Read one line from proc.stdout, respecting a deadline. Returns None on timeout/EOF."""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 0.5))
        if ready:
            line = proc.stdout.readline()
            if not line:
                return None  # EOF
            return line.strip()


def run_one_incremental(solver_name, solver_bin, extra_args, path, run, timeout):
    """Run a solver on an .smt2 file using trace-executor style stdin feeding."""
    commands = parse_commands(path)

    t0 = time.monotonic()
    deadline = t0 + timeout
    last_check_sat_answer = None

    try:
        proc = subprocess.Popen(
            [solver_bin] + extra_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line buffered
        )
    except Exception:
        elapsed = time.monotonic() - t0
        return Result(path, solver_name, run, elapsed, "error", -1, 0, timeout)

    pipe_broken = False

    def send(text):
        nonlocal pipe_broken
        if pipe_broken:
            return False
        try:
            proc.stdin.write(text)
            proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError):
            pipe_broken = True
            return False

    try:
        # Send (set-option :print-success true) first
        if not send("(set-option :print-success true)\n"):
            proc.wait()
            elapsed = time.monotonic() - t0
            sig = -proc.returncode if proc.returncode < 0 else 0
            answer = "crash" if proc.returncode < 0 else "error"
            return Result(path, solver_name, run, elapsed, answer, proc.returncode, sig, timeout)

        resp = _read_line_timeout(proc, deadline)
        if resp is None:
            proc.kill()
            proc.wait()
            elapsed = time.monotonic() - t0
            return Result(path, solver_name, run, elapsed, "timeout", -1, 0, timeout)

        # Feed each command
        for cmd, is_check_sat in commands:
            if time.monotonic() >= deadline:
                break

            if not send(cmd + "\n"):
                break

            if is_check_sat:
                resp = _read_line_timeout(proc, deadline)
                if resp is None:
                    break
                if resp in ("sat", "unsat", "unknown"):
                    last_check_sat_answer = resp
            else:
                resp = _read_line_timeout(proc, deadline)
                if resp is None:
                    break

        # Send exit
        if not pipe_broken and time.monotonic() < deadline:
            send("(exit)\n")

        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        elapsed = time.monotonic() - t0
        sig = -proc.returncode if proc.returncode < 0 else 0

        if proc.returncode < 0:
            answer = "crash"
        elif last_check_sat_answer:
            answer = last_check_sat_answer
        elif elapsed >= timeout:
            answer = "timeout"
        else:
            answer = "error"

        return Result(path, solver_name, run, elapsed, answer, proc.returncode, sig, timeout)

    except Exception:
        try:
            proc.kill()
            proc.wait()
        except Exception:
            pass
        elapsed = time.monotonic() - t0
        return Result(path, solver_name, run, elapsed, "error", -1, 0, timeout)


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


def run_pool(tasks, workers, timeout, on_result, wall_seconds=None, incremental=False):
    """Run solver tasks in parallel using a process pool.

    Args:
        tasks: iterable of (solver_name, binary, extra_args, path, run_idx) tuples
        workers: number of parallel workers
        timeout: per-task timeout in seconds
        on_result: callback(Result) called for each completed task
        wall_seconds: optional wall-clock budget; None means unlimited
        incremental: if True, use trace-executor style stdin feeding

    Returns:
        Number of tasks completed.
    """
    runner = run_one_incremental if incremental else run_one
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
                fut = executor.submit(runner, name, binary, extra_args, path, run_idx, timeout)
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
                        new_fut = executor.submit(runner, name, binary, extra_args, path, run_idx, timeout)
                        pending[new_fut] = True
                    except StopIteration:
                        exhausted = True

    return completed
