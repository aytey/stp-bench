#!/bin/bash
# Runs after cnf-rung.sh: the equality abstraction on/off in the FP arms, KLEE hard-solved list.
set -eu
R=/home/avj/fp-abs-results/v3; B=/home/avj/clones/stp/benchmark-scripts
stamp() { echo "=== $1: $(date '+%F %T') ==="; }
until grep -q "all done" ~/fp-abs-results/cnf-rung.log 2>/dev/null; do sleep 60; done
stamp "KLEE hard-solved list (1,241), 4 arms, 60 s"
cd "$B" && python3 ./run_comparison.py configs/fp_abstraction_eq_onoff.yaml --file-list ~/fp-abs-results/hard-solved.list --timeout 60 --runs 1 --no-revalidate --workers 24 --output "$R/eq_onoff_hard.csv" --force > "$R/eq_onoff_hard.log" 2>&1
stamp "scores"
python3 /home/avj/fp-abs-results/score_ab.py "$R/eq_onoff_hard.csv" "$R/bench-v3/manifest.tsv" 60 fp-bv64 fp-term-only bv64 term-only | tee "$R/eq_onoff_hard.score.txt"
stamp "all done"
