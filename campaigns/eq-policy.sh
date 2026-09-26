#!/bin/bash
# Runs after const-policies.sh: the equality constant-side policy on the KLEE
# hard-solved list and the converted QF_FP corpus.
set -eu
R=/home/avj/fp-abs-results/v3; B=/home/avj/clones/stp/benchmark-scripts; CFG=configs/fp_abstraction_eq_constant_side.yaml
LQ=/home/avj/clones/benchmark-submission/non-incremental/QF_FP/20260824-LatendresseFP-AndrewTeylu
stamp() { echo "=== $1: $(date '+%F %T') ==="; }
until grep -q "all done" ~/fp-abs-results/const-policies.log 2>/dev/null; do sleep 60; done
stamp "KLEE hard-solved list (1,241), 4 arms, 60 s"
cd "$B" && python3 ./run_comparison.py "$CFG" --file-list ~/fp-abs-results/hard-solved.list --timeout 60 --runs 1 --no-revalidate --workers 24 --output "$R/eq_policy_hard.csv" --force > "$R/eq_policy_hard.log" 2>&1
stamp "converted QF_FP (275), 4 arms, 120 s"
cd "$B" && python3 ./run_comparison.py "$CFG" --no-override --dir "$LQ" --timeout 120 --runs 1 --no-revalidate --workers 24 --output "$R/latendresse/eq_policy.csv" --force > "$R/latendresse/eq_policy.log" 2>&1
stamp "scores"
python3 /home/avj/fp-abs-results/score_ab.py "$R/eq_policy_hard.csv" "$R/bench-v3/manifest.tsv" 60 eq-abs eq-exact bv64-eq-abs bv64-eq-exact | tee "$R/eq_policy_hard.score.txt"
echo; python3 /home/avj/fp-abs-results/score_ab.py "$R/latendresse/eq_policy.csv" "$R/latendresse/manifest.tsv" 120 eq-abs eq-exact bv64-eq-abs bv64-eq-exact | tee "$R/latendresse/eq_policy.score.txt"
stamp "all done"
