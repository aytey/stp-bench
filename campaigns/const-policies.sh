#!/bin/bash
# The two constant-operand policies crossed, on the KLEE hard-solved list and
# on the converted QF_FP corpus, one run each, scored per width.
#   nohup ~/fp-abs-results/const-policies.sh > ~/fp-abs-results/const-policies.log 2>&1 &
set -eu
R=/home/avj/fp-abs-results/v3
B=/home/avj/clones/stp/benchmark-scripts
CFG=configs/fp_abstraction_constant_operands.yaml
LQ=/home/avj/clones/benchmark-submission/non-incremental/QF_FP/20260824-LatendresseFP-AndrewTeylu
stamp() { echo "=== $1: $(date '+%F %T') ==="; }
stamp "KLEE hard-solved list (1,241), 4 arms, 60 s"
cd "$B" && python3 ./run_comparison.py "$CFG" --file-list ~/fp-abs-results/hard-solved.list --timeout 60 --runs 1 --no-revalidate --workers 24 \
    --output "$R/const_policies_hard.csv" --force > "$R/const_policies_hard.log" 2>&1
stamp "converted QF_FP (275), 4 arms, 120 s"
cd "$B" && python3 ./run_comparison.py "$CFG" --no-override --dir "$LQ" --timeout 120 --runs 1 --no-revalidate --workers 24 \
    --output "$R/latendresse/const_policies.csv" --force > "$R/latendresse/const_policies.log" 2>&1
stamp "scores"
python3 /home/avj/fp-abs-results/score_ab.py "$R/const_policies_hard.csv" "$R/bench-v3/manifest.tsv" 60 fp-abs-bv-abs fp-abs-bv-exact fp-exact-bv-abs fp-exact-bv-exact | tee "$R/const_policies_hard.score.txt"
echo
python3 /home/avj/fp-abs-results/score_ab.py "$R/latendresse/const_policies.csv" "$R/latendresse/manifest.tsv" 120 fp-abs-bv-abs fp-abs-bv-exact fp-exact-bv-abs fp-exact-bv-exact | tee "$R/latendresse/const_policies.score.txt"
stamp "all done"
