# STP benchmark scripts

> Part of the [floating-point benchmark work](https://github.com/aytey/fp-repro): that repository pins this one with the KLEE fork, the corpus, the replay harness and STP, and has the build recipe and the steps in order.

Run solvers over an SMT-LIB corpus, time them, and compare the results.

```sh
./run_comparison.py configs/stp_vs_bitwuzla.yaml
```

Which binaries run, with which arguments, over which benchmarks, lives in the
YAML config — not in the code. Adding an option set to try is an edit to
`configs/`.

Settings come from four layers, each overriding the one before:

1. `configs/defaults.yaml` — timeout, repeats, revalidation policy, shipped
   with the repo and shared by every experiment.
2. the named experiment file — what this comparison actually changes.
3. `configs/local.yaml` — this machine's binary paths. Gitignored; copy
   `configs/local.example.yaml` to create one.
4. command-line flags — a one-off run.

So raising the timeout everywhere is one edit to `defaults.yaml`, an
experiment file carries only its own solvers, benchmarks and exceptions, and a
host whose builds live somewhere else pins them without touching either.

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

Every key below may appear in either layer. `configs/defaults.yaml` holds the
`run`, `revalidation` and `report` blocks; an experiment file typically sets
only `name`, `output_prefix`, `benchmarks`, `binaries`, `arg_groups` and
`solvers`.

```yaml
name: STP vs Bitwuzla (UF benchmarks)   # shown in the live display
output_prefix: stp_vs_bitwuzla          # <prefix>_<git hashes>.csv

benchmarks:
  dirs: [/mnt/baranem/uf_bench]         # searched recursively for .smt2
  file_list: null                       # or a file of paths, one per line

run:
  timeout: 30.0                         # seconds per solver per file
  runs: 3                               # repeats, medianed
  workers: null                         # null -> CPU count
  wall_hours: 24.0                      # budget for the main pass
  incremental: false                    # true -> feed stdin command-by-command,
                                        #   SMT-COMP trace executor style

revalidation:
  enabled: true
  ratio: 3.0                            # median gap that flags a file
  timeout: null                         # the repeat timeout;
                                        #   null -> max(4 * run.timeout, 120)

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
required across the two layers; every other key has the default shown.
Unknown keys are rejected rather than ignored, in both layers, so a typo
fails immediately instead of silently changing what a 24-hour run measures.

### How the layers merge

`benchmarks`, `run`, `revalidation`, `report`, `binaries` and `arg_groups`
merge key-by-key, so an experiment setting `run.timeout` keeps the inherited
`run.runs`, and an `arg_groups` defined in `defaults.yaml` is usable from any
experiment. Everything else replaces wholesale — in particular `solvers`,
where merging two lists would be guesswork, so an experiment always names its
own in full.

`benchmarks.dirs` and `benchmarks.file_list` are two ways of saying the same
thing, so setting one in an experiment clears an inherited other rather than
leaving both in play.

A missing file at any layer is not an error: the built-in defaults match what
`defaults.yaml` ships with, and most hosts need no `local.yaml` at all.
`--no-defaults` / `--defaults PATH` and `--no-override` / `--override PATH`
control the bottom and top layers.

### Host-local paths

Experiment configs name absolute binary paths, and those differ between
machines. Rather than fork the config, give each host a `configs/local.yaml`:

```yaml
binaries:
  stp: /home/avj/clones/stp/uf_updated/build/stp
  bitwuzla: /home/avj/clones/bitwuzla/build/src/main/bitwuzla
```

It is applied last, so it wins, and `binaries` merges key-by-key — naming one
binary leaves the rest of the experiment alone. Anything else in the schema
works there too (`run.workers` on a shared machine, a smaller corpus). The
banner and the manifest both record which layers were in play.

The first solver is the baseline. Every other one is scored against it, both
in the live display and in the final tables, so a run can carry as many option
sets as there is time for.

## Command-line overrides

`--timeout`, `--runs`, `--workers`, `--wall-hours`, `--incremental`,
`--dir`, `--file-list` and `--output` override both config layers for one run.
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
- `configs/defaults.yaml` — settings shared by every experiment
- `configs/` — experiment definitions, layered over those
- `benchlib/` — config loading, solver execution, result handling, live display
- `deprecated/` — superseded scripts, kept for reference
