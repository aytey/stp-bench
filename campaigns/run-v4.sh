#!/bin/bash
# The v4 campaign: five arms over bench-v3, three runs at 60 s with
# revalidation, then the -s statistics pass, the ablations, and the draft's
# data regenerated from all three.
#
# Writes only into its own output directory, which must not already exist --
# an earlier run of this script's v3 ancestor overwrote a finished campaign's
# raw CSV with a third of a restarted one, which is what the guard below is
# for. OUT=... to put it elsewhere, FORCE=1 to write into a directory that
# is already there.
set -eu
R=${OUT:-/home/avj/fp-abs-results/v4}
P=/home/avj/clones/fp-abs-paper
B=/home/avj/clones/stp/benchmark-scripts
M=/mnt/baranem
workers=18
if [ -e "$R" ] && [ -n "$(ls -A "$R" 2>/dev/null)" ] && [ "${FORCE:-0}" != "1" ]; then
    echo "$R already exists and is not empty; refusing to write into it."
    echo "Point OUT= somewhere new, or set FORCE=1 if overwriting is what you want."
    exit 1
fi
mkdir -p $R
stamp() { echo "=== $1: $(date '+%F %T') ==="; }

stamp "bits bindings in bench-v3 (both counts must be 0)"
python3 $P/scripts/check_bits_bindings.py $M/fp-benchmarks/bench-v3/queries $M/fp-benchmarks/bench-v3/manifest.tsv || { echo "BITS CHECK FAILED -- stopping"; exit 1; }
rm -rf $R/bench-v3 && cp -r $M/fp-benchmarks/bench-v3 $R/bench-v3 && echo "corpus copied to $R/bench-v3 ($(ls $R/bench-v3/queries | wc -l) queries)"
Q=$R/bench-v3/queries; MAN=$R/bench-v3/manifest.tsv

stamp "main campaign: five arms, three runs, 60 s + revalidation"
cd $B && python3 ./run_comparison.py configs/fp_abstraction_vs_bitwuzla.yaml --no-override --dir $Q --workers ${workers} --output $R/fp_abs_vs_bwz.csv --force > $R/main.log 2>&1
tail -3 $R/main.log

stamp "stats pass: headline configuration with -s"
rm -rf $R/stats-fp-bv64
python3 $P/scripts/collect_stats.py /home/avj/clones/stp/campaign-builds/fp-cegar-4c4da0a4ae1e/build/stp $Q $R/stats-fp-bv64 --workers 12 --timeout 120 \
  -- --cadical --bv-eq-abstraction=1 --bv-term-abstraction=1 --fp-abstraction=1 > $R/stats.log 2>&1
python3 $P/scripts/parse_stats.py $R/stats-fp-bv64 $MAN $R --timeout 120 | tail -2

stamp "ablations"
cd $B && python3 ./run_comparison.py configs/fp_abstraction_ablations.yaml --no-override --dir $Q --workers ${workers} --output $R/fp_abs_ablate.csv --force > $R/ablate.log 2>&1
tail -2 $R/ablate.log

stamp "regenerate the draft's data"
cd $P && python3 scripts/make_plot_data.py $R/fp_abs_vs_bwz.csv $R/fp_abs_vs_bwz_revalidation.csv $MAN data \
  && python3 scripts/parse_stats.py $R/stats-fp-bv64 $MAN data --timeout 120 > /dev/null \
  && python3 scripts/make_ablation_rows.py $R/fp_abs_ablate.csv $MAN data \
  && lualatex -interaction=nonstopmode -halt-on-error main.tex > build1.log 2>&1 && lualatex -interaction=nonstopmode -halt-on-error main.tex > build2.log 2>&1 \
  && echo "draft rebuilt: $(pdfinfo main.pdf | grep Pages)"
stamp "all done"
