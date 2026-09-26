#!/bin/bash
# The v5 campaign: four STP arms on the KLEE-derived corpus, into a fresh
# directory. Main pass plus revalidation only -- the ablations are a separate
# table and are deferred so this finishes overnight rather than at midday.
#
#   nohup ~/fp-abs-results/run-v5.sh > ~/fp-abs-results/run-v5.log 2>&1 &
#
# Set OUT= to write somewhere else. It refuses a non-empty directory unless
# FORCE=1, because a previous campaign was lost to exactly that.
set -eu
R=${OUT:-/home/avj/fp-abs-results/v5}
P=/home/avj/clones/fp-abs-paper
B=/home/avj/clones/stp/benchmark-scripts
M=/mnt/baranem
STP=/home/avj/clones/stp/campaign-builds/fp-cegar-snapshot/build/stp
CFG=configs/fp_abstraction_v5.yaml
workers=${WORKERS:-18}

if [ -e "$R" ] && [ -n "$(ls -A "$R" 2>/dev/null)" ] && [ "${FORCE:-0}" != "1" ]; then
    echo "$R already exists and is not empty; refusing to write into it."
    echo "Point OUT= somewhere new, or set FORCE=1 if overwriting is what you want."
    exit 1
fi
mkdir -p "$R"
stamp() { echo "=== $1: $(date '+%F %T') ==="; }

stamp "preflight"
[ -x "$STP" ] || { echo "missing binary: $STP"; exit 1; }
"$STP" --help 2>&1 | grep -q -- --refinement-trail-reuse || {
  echo "the STP binary has no --refinement-trail-reuse; wrong build"; exit 1; }
echo "  stp $("$STP" --version 2>&1 | grep -oE 'SHA string [0-9a-f]{8}')"
echo "  workers ${workers}, arms: $(python3 -c "
import yaml; print(', '.join(s['name'] for s in yaml.safe_load(open('$B/$CFG'))['solvers']))")"

stamp "bits bindings in bench-v3 (both counts must be 0)"
python3 $P/scripts/check_bits_bindings.py $M/fp-benchmarks/bench-v3/queries \
    $M/fp-benchmarks/bench-v3/manifest.tsv || { echo "corpus check failed"; exit 1; }
rm -rf "$R/bench-v3" && cp -r $M/fp-benchmarks/bench-v3 "$R/bench-v3" \
    && echo "  corpus copied to $R/bench-v3 ($(ls "$R/bench-v3/queries" | wc -l) queries)"
Q=$R/bench-v3/queries; MAN=$R/bench-v3/manifest.tsv

stamp "main campaign: four arms, three runs, 60 s + revalidation at 240 s"
cd "$B" && python3 ./run_comparison.py "$CFG" --no-override --dir "$Q" \
    --workers ${workers} --output "$R/fp_abs_vs_bwz.csv" --force > "$R/main.log" 2>&1
tail -3 "$R/main.log"

stamp "stats pass: headline configuration with -s"
rm -rf "$R/stats-fp-bv64"
python3 $P/scripts/collect_stats.py "$STP" "$Q" "$R/stats-fp-bv64" \
    -- --cadical --bv-eq-abstraction=1 --bv-term-abstraction=1 \
       --refinement-trail-reuse=0 --fp-abstraction=1 > "$R/stats.log" 2>&1
python3 $P/scripts/parse_stats.py "$R/stats-fp-bv64" "$MAN" "$R" --timeout 120 | tail -2

stamp "regenerate the draft's data"
cd $P && python3 scripts/make_plot_data.py "$R/fp_abs_vs_bwz.csv" \
      "$R/fp_abs_vs_bwz_revalidation.csv" "$MAN" data \
  && python3 scripts/parse_stats.py "$R/stats-fp-bv64" "$MAN" data --timeout 120 > /dev/null \
  && lualatex -interaction=nonstopmode -halt-on-error main.tex > build1.log 2>&1 \
  && lualatex -interaction=nonstopmode -halt-on-error main.tex > build2.log 2>&1 \
  && echo "  draft rebuilt: $(pdfinfo main.pdf | grep Pages)"

stamp "all done"
echo "The ablations were deferred. To run them against the same binary:"
echo "  cd $B && python3 ./run_comparison.py configs/fp_abstraction_ablations.yaml \\"
echo "      --no-override --dir $Q --workers ${workers} --output $R/fp_abs_ablate.csv --force"
