#!/usr/bin/env python3
"""
Run a solver comparison described by a YAML config.

Which binaries run, with which arguments, over which benchmarks, is entirely
in the config file — see configs/ for the ones in use and README.md for the
schema. Settings layer: configs/defaults.yaml, then the named experiment
file, then the host-local configs/local.yaml, then any command-line flag.

Output: a CSV with one row per (file, solver, run) triple, plus a revalidation
CSV for the files whose first-pass results disagreed.

Usage:
    ./run_comparison.py configs/stp_vs_bitwuzla.yaml
    ./run_comparison.py configs/stp_builds.yaml --runs 1 --timeout 10
"""

import argparse
import sys
from pathlib import Path

from benchlib.config import DEFAULTS_PATH, LOCAL_PATH, ConfigError, load_config
from benchlib.experiment import run_experiment


def main():
    parser = argparse.ArgumentParser(
        description="Run a solver comparison from a YAML config")
    parser.add_argument("config", type=Path, help="Experiment YAML (see configs/)")
    parser.add_argument("--defaults", type=Path, default=DEFAULTS_PATH,
                        help=f"Config layered under it (default: {DEFAULTS_PATH})")
    parser.add_argument("--no-defaults", action="store_true",
                        help="Ignore the defaults file; use the built-in defaults")
    parser.add_argument("--override", type=Path, default=LOCAL_PATH,
                        help=f"Config layered on top, for host-specific binary "
                             f"paths (default: {LOCAL_PATH}, if it exists)")
    parser.add_argument("--no-override", action="store_true",
                        help="Ignore the host-local override file")
    parser.add_argument("--dir", type=Path, nargs="*", default=None,
                        help="Override benchmarks.dirs")
    parser.add_argument("--file-list", type=Path, default=None,
                        help="Override benchmarks.file_list")
    parser.add_argument("--timeout", type=float, default=None,
                        help="Per-solver timeout in seconds")
    parser.add_argument("--runs", type=int, default=None,
                        help="Number of runs per file per solver")
    parser.add_argument("--wall-hours", type=float, default=None,
                        help="Wall-clock budget for the main pass")
    parser.add_argument("--workers", type=int, default=None,
                        help="Parallel workers (default: config, else CPU count)")
    parser.add_argument("--incremental", action="store_true", default=None,
                        help="Feed input via stdin line-by-line "
                             "(SMT-COMP trace executor style)")
    parser.add_argument("--no-revalidate", action="store_true",
                        help="Skip the longer-timeout revalidation pass")
    parser.add_argument("--solvers", default=None,
                        help="Comma-separated subset of solver names to run; "
                             "the first one listed becomes the baseline")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output CSV (default: auto-generated with git hashes)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite an existing results CSV")
    args = parser.parse_args()

    try:
        config = load_config(
            args.config,
            defaults=None if args.no_defaults else args.defaults,
            override=None if args.no_override else args.override)

        if args.solvers:
            wanted = [n.strip() for n in args.solvers.split(",") if n.strip()]
            by_name = {s.name: s for s in config.solvers}
            missing = [n for n in wanted if n not in by_name]
            if missing:
                raise ConfigError(
                    f"--solvers: {', '.join(missing)} not in {args.config} "
                    f"(available: {', '.join(by_name)})")
            if len(wanted) < 2:
                raise ConfigError("--solvers: name at least two solvers")
            config.solvers = [by_name[n] for n in wanted]
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.dir is not None:
        config.dirs, config.file_list = args.dir, None
    if args.file_list is not None:
        config.file_list, config.dirs = args.file_list, []
    if args.timeout is not None:
        config.timeout = args.timeout
    if args.runs is not None:
        if args.runs < 1:
            parser.error("--runs must be at least 1")
        config.runs = args.runs
    if args.wall_hours is not None:
        config.wall_hours = args.wall_hours
    if args.workers is not None:
        if args.workers < 1:
            parser.error("--workers must be at least 1")
        config.workers = args.workers
    if args.incremental is not None:
        config.incremental = args.incremental
    if args.no_revalidate:
        config.revalidate = False

    return run_experiment(config, output=args.output, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
