#!/bin/bash
# Diagnostic campaign: ddef09bd vs e2f9640e (both optimised builds), with and
# without --fp-abstraction-restart-width=128, on both corpora back to back.
# One run per query, no revalidation; 24 workers as requested (the box has
# 24 cores, so per-query times are inflated, equally for every arm).
#
#   nohup ~/fp-abs-results/old-vs-new-full.sh > ~/fp-abs-results/old-vs-new-full.log 2>&1 &
set -eu
R=/home/avj/fp-abs-results/v3
P=/home/avj/clones/fp-abs-paper
B=/home/avj/clones/stp/benchmark-scripts
CFG=configs/fp_abstraction_old_vs_new_full.yaml
KQ=$R/bench-v3/queries; KMAN=$R/bench-v3/manifest.tsv
LQ=/home/avj/clones/benchmark-submission/non-incremental/QF_FP/20260824-LatendresseFP-AndrewTeylu
LR=$R/latendresse; LMAN=$LR/manifest.tsv
W=24
stamp() { echo "=== $1: $(date '+%F %T') ==="; }

for bin in /home/avj/clones/stp/campaign-builds/fp-cegar-ddef09bd6507/build/stp \
           /home/avj/clones/stp/campaign-builds/fp-cegar-e2f9640eafee/build/stp; do
  [ -x "$bin" ] || { echo "missing binary: $bin"; exit 1; }
  echo "$($bin --version | sed -n 2p)"
done
[ -d "$KQ" ] || { echo "missing corpus: $KQ"; exit 1; }
[ -d "$LQ" ] || { echo "missing corpus: $LQ"; exit 1; }
mkdir -p "$LR"
[ -f "$LMAN" ] || python3 "$P/scripts/make_manifest.py" "$LQ" "$LMAN" --family latendresse --library-from-name '^([A-Za-z]+)'

stamp "KLEE bench-v3: $(ls "$KQ" | wc -l) queries, 4 arms, 60 s"
cd "$B" && python3 ./run_comparison.py "$CFG" --no-override --dir "$KQ" --workers $W \
    --output "$R/old_vs_new_full.csv" --force > "$R/old_vs_new_full.log" 2>&1
tail -3 "$R/old_vs_new_full.log"

stamp "converted QF_FP: $(find "$LQ" -name '*.smt2' | wc -l) queries, 4 arms, 120 s"
cd "$B" && python3 ./run_comparison.py "$CFG" --no-override --dir "$LQ" --timeout 120 --workers $W \
    --output "$LR/old_vs_new_full.csv" --force > "$LR/old_vs_new_full.log" 2>&1
tail -3 "$LR/old_vs_new_full.log"

stamp "scores"
python3 /home/avj/fp-abs-results/score_ab.py "$R/old_vs_new_full.csv" "$KMAN" 60 | tee "$R/old_vs_new_full.score.txt"
echo
python3 /home/avj/fp-abs-results/score_ab.py "$LR/old_vs_new_full.csv" "$LMAN" 120 | tee "$LR/old_vs_new_full.score.txt"
stamp "all done"
