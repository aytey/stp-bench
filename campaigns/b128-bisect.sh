#!/bin/bash
# Waits for the v4 script to finish, then splits the binary128 regression
# between upstream's commits and the equality constant-side policy.
set -eu
R=/home/avj/fp-abs-results/b128; B=/home/avj/clones/stp/benchmark-scripts
mkdir -p $R
stamp() { echo "=== $1: $(date '+%F %T') ==="; }
while pgrep -f "[r]un-v4.sh" > /dev/null || pgrep -f "[c]ollect_stats|[r]un_comparison" > /dev/null; do sleep 120; done
stamp "the v4 script is done; starting the bisect (1,667 binary128 queries, 3 arms, 3 runs, 60 s)"
cd "$B" && python3 ./run_comparison.py configs/b128_regression_bisect.yaml --no-override \
    --file-list /home/avj/fp-abs-results/b128.list --timeout 60 --runs 3 --no-revalidate --workers 18 \
    --output "$R/b128.csv" --force > "$R/b128.log" 2>&1
stamp "scores"
python3 /home/avj/fp-abs-results/score_ab.py "$R/b128.csv" /home/avj/fp-abs-results/v4/bench-v3/manifest.tsv 60 old old-eq new | tee "$R/b128.score.txt"
stamp "all done"
