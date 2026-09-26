#!/bin/bash
set -eu
R=/home/avj/fp-abs-results/v3; B=/home/avj/clones/stp/benchmark-scripts; CFG=configs/fp_abstraction_policy_defaults.yaml
LQ=/home/avj/clones/benchmark-submission/non-incremental/QF_FP/20260824-LatendresseFP-AndrewTeylu
stamp() { echo "=== $1: $(date '+%F %T') ==="; }
stamp "KLEE hard-solved list (1,241), 4 arms, 60 s"
cd "$B" && python3 ./run_comparison.py "$CFG" --file-list ~/fp-abs-results/hard-solved.list --timeout 60 --runs 1 --no-revalidate --workers 24 --output "$R/policy_defaults_hard.csv" --force > "$R/policy_defaults_hard.log" 2>&1
stamp "converted QF_FP (275), 4 arms, 120 s"
cd "$B" && python3 ./run_comparison.py "$CFG" --no-override --dir "$LQ" --timeout 120 --runs 1 --no-revalidate --workers 24 --output "$R/latendresse/policy_defaults.csv" --force > "$R/latendresse/policy_defaults.log" 2>&1
stamp "scores"
python3 /home/avj/fp-abs-results/score_ab.py "$R/policy_defaults_hard.csv" "$R/bench-v3/manifest.tsv" 60 pre-change proposed committed fp-abs-only | tee "$R/policy_defaults_hard.score.txt"
echo; python3 /home/avj/fp-abs-results/score_ab.py "$R/latendresse/policy_defaults.csv" "$R/latendresse/manifest.tsv" 120 pre-change proposed committed fp-abs-only | tee "$R/latendresse/policy_defaults.score.txt"
stamp "all done"
