#!/bin/bash
# The whole pipeline from a fresh capture of the three STP arms to every
# campaign the draft's tables come from, with the fixed KLEE, the fixed
# splitter and the fixed inventory. The Bitwuzla arm is carried over from
# capture4 (hard-linked into capture5/bwz), since its builder never had the
# bug. Everything durable lands under ~/fp-abs-results/v3; the corpus is
# copied off tmpfs as soon as it exists.
set -eu
R=/home/avj/fp-abs-results/v3
P=/home/avj/clones/fp-abs-paper
B=/home/avj/clones/stp/benchmark-scripts
M=/mnt/baranem
mkdir -p $R
stamp() { echo "=== $1: $(date '+%F %T') ==="; }

stamp "capture5: exact/fpabs/fpbv with the fixed KLEE"
rm -rf $M/capture5/exact $M/capture5/fpabs $M/capture5/fpbv $M/capture5/yield.txt
cd $M && ./capture5.sh 16 120 60 > $M/capture5.log 2>&1
grep -c "rc=" $M/capture5/yield.txt | sed 's/^/jobs finished: /' || true
awk '{split($3,a,"="); if (a[2]!=0) c[$2" rc="a[2]]++} END{for(k in c) print "  abnormal "k": "c[k]}' $M/capture5/yield.txt || true

stamp "inventory"
python3 $M/inventory.py $M/capture5 $M/capture5/inventory.tsv 16 > $M/capture5/inventory.log 2>&1
tail -3 $M/capture5/inventory.log

stamp "curate bench-v3"
rm -rf $M/fp-benchmarks/bench-v3
python3 $M/curate.py $M/capture5/inventory.tsv $M/capture5 $M/fp-benchmarks/bench-v3
echo "bits bindings in the new STP-family queries (both counts must be 0):"
python3 $P/scripts/check_bits_bindings.py $M/fp-benchmarks/bench-v3/queries $M/fp-benchmarks/bench-v3/manifest.tsv || { echo "BITS CHECK FAILED -- stopping"; exit 1; }
rm -rf $R/bench-v3 && cp -r $M/fp-benchmarks/bench-v3 $R/bench-v3 && echo "corpus copied to $R/bench-v3 ($(ls $R/bench-v3/queries | wc -l) queries)"
Q=$R/bench-v3/queries; MAN=$R/bench-v3/manifest.tsv

stamp "main campaign: seven arms, three runs, 60 s + revalidation"
cd $B && python3 ./run_comparison.py configs/fp_abstraction_vs_bitwuzla.yaml --no-override --dir $Q --workers 16 --output $R/fp_abs_vs_bwz.csv --force > $R/main.log 2>&1
tail -3 $R/main.log

stamp "stats pass: headline configuration with -s"
python3 $P/scripts/collect_stats.py /home/avj/clones/stp/fp_cegar/build-rel/stp $Q $R/stats-fp-bv64 --workers 12 --timeout 120 \
  -- --cadical --bv-eq-abstraction=1 --bv-term-abstraction=1 --fp-abstraction=1 > $R/stats.log 2>&1
python3 $P/scripts/parse_stats.py $R/stats-fp-bv64 $MAN $R --timeout 120 | tail -2

stamp "ablations"
cd $B && python3 ./run_comparison.py configs/fp_abstraction_ablations.yaml --no-override --dir $Q --workers 16 --output $R/fp_abs_ablate.csv --force > $R/ablate.log 2>&1
tail -2 $R/ablate.log

stamp "regenerate the draft's data"
cd $P && python3 scripts/make_plot_data.py $R/fp_abs_vs_bwz.csv $R/fp_abs_vs_bwz_revalidation.csv $MAN data \
  && python3 scripts/parse_stats.py $R/stats-fp-bv64 $MAN data --timeout 120 > /dev/null \
  && python3 scripts/make_ablation_rows.py $R/fp_abs_ablate.csv $MAN data \
  && lualatex -interaction=nonstopmode -halt-on-error main.tex > build1.log 2>&1 && lualatex -interaction=nonstopmode -halt-on-error main.tex > build2.log 2>&1 \
  && echo "draft rebuilt: $(pdfinfo main.pdf | grep Pages)"
stamp "all done"
