#!/bin/bash
# The automatic constant-operand policy, both corpora. Needs build-rel-exp
# rebuilt with the tri-state knob.
set -eu
R=/home/avj/fp-abs-results/v3; B=/home/avj/clones/stp/benchmark-scripts; CFG=configs/fp_abstraction_constant_operands_auto.yaml
LQ=/home/avj/clones/benchmark-submission/non-incremental/QF_FP/20260824-LatendresseFP-AndrewTeylu
stamp() { echo "=== $1: $(date '+%F %T') ==="; }
/home/avj/clones/stp/fp_cegar/build-rel-exp/stp --help 2>&1 | grep -q "'auto' (the default) does so when the configuration" || { echo "build-rel-exp does not have the automatic policy; build it first"; exit 1; }
stamp "KLEE hard-solved list (1,241), 4 arms, 60 s"
cd "$B" && python3 ./run_comparison.py "$CFG" --file-list ~/fp-abs-results/hard-solved.list --timeout 60 --runs 1 --no-revalidate --workers 24 --output "$R/auto_policy_hard.csv" --force > "$R/auto_policy_hard.log" 2>&1
stamp "converted QF_FP (275), 4 arms, 120 s"
cd "$B" && python3 ./run_comparison.py "$CFG" --no-override --dir "$LQ" --timeout 120 --runs 1 --no-revalidate --workers 24 --output "$R/latendresse/auto_policy.csv" --force > "$R/latendresse/auto_policy.log" 2>&1
stamp "scores"
python3 /home/avj/fp-abs-results/score_ab.py "$R/auto_policy_hard.csv" "$R/bench-v3/manifest.tsv" 60 auto on off off-both | tee "$R/auto_policy_hard.score.txt"
echo; python3 /home/avj/fp-abs-results/score_ab.py "$R/latendresse/auto_policy.csv" "$R/latendresse/manifest.tsv" 120 auto on off off-both | tee "$R/latendresse/auto_policy.score.txt"
stamp "all done"
