# STP benchmark scripts

Run solvers over an SMT-LIB corpus, time them, and compare the results.

```sh
./run_comparison.py configs/stp_vs_bitwuzla.yaml
```

Which binaries run, with which arguments, over which benchmarks, lives in the
YAML config — not in the code. Adding an option set to try is an edit to
`configs/`.

## What a run produces

For output `<prefix>_<hashes>.csv`:

| File | Contents |
| --- | --- |
| `<name>.csv` | one row per (file, solver, run): elapsed, answer, exit code, signal |
| `<name>_manifest.json` | what produced it — config, binaries, arguments, git hashes |
| `<name>_revalidation.csv` | the flagged files, rerun at a longer timeout |
| `<name>_answer_disagreements.csv` | files where two solvers returned opposite conclusive answers |

The main pass runs every file at the configured timeout. Any file where the
results disagree — a sat/unsat split, one solver timing out where another
answered, or a median gap of `revalidation.ratio` either way — is then rerun at
four times the timeout (or `revalidation.timeout`), and those results replace
the first-pass ones in the final tables.

`unknown` is treated as not comparable, in either direction: a solver that does
not support a query answers `unknown` in milliseconds without starting work,
and timing that against a solver that does solve it measures nothing.

Then analyse:

```sh
./summarize_comparison.py run.csv run_revalidation.csv --by-theory
./plot_comparison.py      run.csv run_revalidation.csv --outdir plots
./compare_runs.py         old.csv new.csv                  # same solver, two runs
```

`summarize_comparison.py` and `plot_comparison.py` default to the baseline and
the first alternative from the manifest, and take `--solver-a` / `--solver-b`
to pick a different pair. `compare_runs.py` follows one solver across two CSVs,
so it takes a single `--solver`, defaulting to the baseline.

## Config schema

```yaml
name: STP vs Bitwuzla (UF benchmarks)   # shown in the live display
output_prefix: stp_vs_bitwuzla          # <prefix>_<git hashes>.csv

benchmarks:
  dirs: [/mnt/baranem/uf_bench]         # searched recursively for .smt2
  file_list: null                       # or a file of paths, one per line

run:
  timeout: 30.0                         # seconds per solver per file
  runs: 3                               # medianed
  workers: null                         # null -> CPU count
  wall_hours: 24.0                      # budget for the main pass
  incremental: false                    # true -> feed stdin command-by-command,
                                        #   SMT-COMP trace executor style

revalidation:
  enabled: true
  ratio: 3.0                            # median gap that flags a file
  timeout: null                         # null -> max(4 * timeout, 120)

report:
  virtual_timeouts: [24, 120]           # score table cutoffs, in seconds

binaries:                               # names to reuse below
  stp: /home/avj/clones/stp/master/build-release/stp
  bitwuzla: /home/avj/clones/bitwuzla/main/build/src/main/bitwuzla

arg_groups:                             # argument sets to reuse below
  common: [--uninterpreted-functions, --array-equality]
  cegar:  [--uf-ackermann=off]

solvers:                                # first entry is the baseline
  - name: bitwuzla
    binary: bitwuzla                    # a name from `binaries`, or a path
  - name: stp-cegar
    binary: stp
    arg_groups: [common, cegar]         # concatenated, in order
    args: [--uf-inject-args=1]          # then these
```

Only `solvers` and one of `benchmarks.dirs` / `benchmarks.file_list` are
required; every other key has the default shown. Unknown keys are rejected
rather than ignored, so a typo fails immediately instead of silently changing
what a 24-hour run measures.

The first solver is the baseline. Every other one is scored against it, both
in the live display and in the final tables, so a run can carry as many option
sets as there is time for.

## Command-line overrides

`--timeout`, `--runs`, `--workers`, `--wall-hours`, `--incremental`,
`--dir`, `--file-list` and `--output` override the config for one run.
`--no-revalidate` skips the second pass, and `--solvers a,b,c` runs a named
subset — the first one listed becomes the baseline.

The auto-generated name is built from git hashes alone, so a rerun of the same
binaries — a `--solvers` subset included — lands on the name the earlier run
used. That is refused rather than overwritten; pass `--output` or `--force`.

```sh
./run_comparison.py configs/stp_vs_bitwuzla.yaml --solvers bitwuzla,stp-cegar --runs 1
```

## Layout

- `run_comparison.py` — the runner
- `configs/` — experiment definitions
- `benchlib/` — config loading, solver execution, result handling, live display
- `deprecated/` — superseded scripts, kept for reference
