#!/bin/bash
# Runs after eq-policy.sh: the CNF rung on the converted QF_FP corpus.
set -eu
R=/home/avj/fp-abs-results/v3; B=/home/avj/clones/stp/benchmark-scripts
stamp() { echo "=== $1: $(date '+%F %T') ==="; }
until grep -q "all done" ~/fp-abs-results/eq-policy.log 2>/dev/null; do sleep 60; done
stamp "converted QF_FP (275), 5 arms, 120 s"
cd "$B" && python3 ./run_comparison.py configs/fp_cnf_rung_latendresse.yaml --no-override --timeout 120 --runs 1 --no-revalidate --workers 24 --output "$R/latendresse/cnf_rung.csv" --force > "$R/latendresse/cnf_rung.log" 2>&1
stamp "scores"
python3 /home/avj/fp-abs-results/score_ab.py "$R/latendresse/cnf_rung.csv" "$R/latendresse/manifest.tsv" 120 exact-auto exact-gia-low exact-new-medium bv64 term-only fp-bv64-gia-low fp-term-only | tee "$R/latendresse/cnf_rung.score.txt"
stamp "all done"
