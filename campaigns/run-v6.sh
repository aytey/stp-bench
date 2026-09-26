#!/bin/bash
# The final campaign. Every configuration at both trail-reuse settings, in one
# pass: one binary, one corpus copy, one load regime, every flag explicit.
#
#   nohup ~/fp-abs-results/run-v6.sh > ~/fp-abs-results/run-v6.log 2>&1 &
#
# Roughly a day. Set OUT= to write elsewhere. It refuses a non-empty directory
# unless FORCE=1, because a campaign was lost to exactly that.
set -eu
R=${OUT:-/home/avj/fp-abs-results/v6}
P=/home/avj/clones/fp-abs-paper
B=/home/avj/clones/stp/benchmark-scripts
M=/mnt/baranem
STP=/home/avj/clones/stp/campaign-builds/fp-cegar-dc22dd12/build/stp
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
  echo "the binary has no --refinement-trail-reuse; wrong build"; exit 1; }
echo "  stp $("$STP" --version 2>&1 | grep -oE 'SHA string [0-9a-f]{8}'), ${workers} workers"
echo "  main arms:     $(python3 -c "
import yaml; print(', '.join(s['name'] for s in yaml.safe_load(open('$B/configs/fp_abstraction_v6.yaml'))['solvers']))")"
echo "  ablation arms: $(python3 -c "
import yaml; print(len(yaml.safe_load(open('$B/configs/fp_abstraction_v6_ablations.yaml'))['solvers']))")"

stamp "bits bindings in bench-v3 (both counts must be 0)"
python3 $P/scripts/check_bits_bindings.py $M/fp-benchmarks/bench-v3/queries \
    $M/fp-benchmarks/bench-v3/manifest.tsv || { echo "corpus check failed"; exit 1; }
rm -rf "$R/bench-v3" && cp -r $M/fp-benchmarks/bench-v3 "$R/bench-v3" \
    && echo "  corpus copied to $R/bench-v3 ($(ls "$R/bench-v3/queries" | wc -l) queries)"
Q=$R/bench-v3/queries; MAN=$R/bench-v3/manifest.tsv

stamp "main campaign: eight arms, three runs, 60 s + revalidation at 240 s"
cd "$B" && python3 ./run_comparison.py configs/fp_abstraction_v6.yaml --no-override \
    --dir "$Q" --workers ${workers} --output "$R/fp_abs_v6.csv" --force > "$R/main.log" 2>&1
tail -3 "$R/main.log"

stamp "ablations: fourteen arms, one run, 120 s"
cd "$B" && python3 ./run_comparison.py configs/fp_abstraction_v6_ablations.yaml --no-override \
    --dir "$Q" --workers ${workers} --output "$R/fp_abs_v6_ablate.csv" --force > "$R/ablate.log" 2>&1
tail -2 "$R/ablate.log"

stamp "stats pass: the headline configuration at both trail settings"
for t in 0 1; do
  rm -rf "$R/stats-trail$t"
  python3 $P/scripts/collect_stats.py "$STP" "$Q" "$R/stats-trail$t" \
      -- --cadical --bv-eq-abstraction=1 --bv-term-abstraction=1 \
         --fp-abstraction=1 --refinement-trail-reuse=$t > "$R/stats-trail$t.log" 2>&1
  echo "  trail=$t:"
  python3 $P/scripts/parse_stats.py "$R/stats-trail$t" "$MAN" "$R/stats-trail$t" --timeout 120 | tail -2
done

stamp "the ordering, at both settings"
python3 - "$R/fp_abs_v6.csv" "$MAN" <<'PY'
import csv, collections, os, statistics, sys
runs = collections.defaultdict(list)
for r in csv.DictReader(open(sys.argv[1])):
    runs[(os.path.basename(r["path"]), r["solver"])].append((float(r["elapsed"]), r["answer"]))
man = {r["file"]: int(r["maxwidth"]) for r in csv.DictReader(open(sys.argv[2]), delimiter="\t")}
VT = 120.0
def med(f, a):
    rs = runs.get((f, a))
    if not rs: return None
    ans = collections.Counter(x for _, x in rs).most_common(1)[0][0]
    m = statistics.median(t for t, _ in rs)
    return m if (ans in ("sat", "unsat") and m <= VT) else None
files = sorted({f for f, _ in runs})
chain = ["stp-exact", "stp-bv64", "stp-fp-bv64-all", "stp-fp-bv64"]
for label, suffix in (("TRAIL OFF", ""), ("TRAIL ON", "-trail")):
    print("  === %s ===" % label)
    for w in (16, 32, 64, 128, None):
        fs = [f for f in files if (man.get(f) == w if w else True)]
        vals = [sum(1 for f in fs if med(f, a + suffix) is not None) for a in chain]
        ok = all(vals[i] <= vals[i+1] for i in range(len(vals) - 1))
        print("    %-9s %s   %s" % ("binary%d" % w if w else "all",
              "  <=  ".join("%s %d" % (a.replace("stp-", ""), v) for a, v in zip(chain, vals)),
              "monotone" if ok else "VIOLATED"))
PY

stamp "regenerate the draft's data"
cd $P && python3 scripts/make_plot_data.py "$R/fp_abs_v6.csv" \
      "$R/fp_abs_v6_revalidation.csv" "$MAN" data \
  && python3 scripts/parse_stats.py "$R/stats-trail0" "$MAN" data --timeout 120 > /dev/null \
  && python3 scripts/make_ablation_rows.py "$R/fp_abs_v6_ablate.csv" "$MAN" data \
  && python3 scripts/make_sensitivity_rows.py "$R/fp_abs_v6.csv" "$R/fp_abs_v6_revalidation.csv" "$MAN" data \
  && lualatex -interaction=nonstopmode -halt-on-error main.tex > build1.log 2>&1 \
  && lualatex -interaction=nonstopmode -halt-on-error main.tex > build2.log 2>&1 \
  && echo "  draft rebuilt: $(pdfinfo main.pdf | grep Pages)"

stamp "trail reuse where there is no floating point"
# The evidence an upstream default change needs, which the float corpus cannot
# give: a QF_BV or QF_UFBV query has no float width to key a rule on, and those
# are the users such a change reaches. Runs last, after the campaign and the
# draft, so nothing competes with the numbers the paper depends on.
T=$R/trail-pure-bv
mkdir -p "$T"
for corpus in QF_UFBV/20241113-Certora QF_BV/20170501-Heizmann-UltimateAutomizer QF_BV/2018-Goel-hwbench; do
  QB=/mnt/baranem/smt2_problems/non-incremental/$corpus
  tag=$(echo "$corpus" | tr '/' '-')
  [ -d "$QB" ] || { echo "  skipping missing corpus $corpus"; continue; }
  echo "  --- $corpus ($(find "$QB" -name '*.smt2' | wc -l) queries) ---"
  cd "$B" && python3 ./run_comparison.py configs/trail_reuse_pure_bv.yaml --no-override \
      --dir "$QB" --workers ${workers} --output "$T/$tag.csv" --force > "$T/$tag.log" 2>&1 || {
        echo "    run failed on $corpus"; continue; }
  python3 - "$T/$tag.csv" <<'PYEOF'
import csv, collections, os, statistics, sys
runs = collections.defaultdict(list)
for r in csv.DictReader(open(sys.argv[1])):
    runs[(os.path.basename(r["path"]), r["solver"])].append((float(r["elapsed"]), r["answer"]))
arms = sorted({a for _, a in runs}); files = sorted({f for f, _ in runs})
VT = 60.0
def med(f, a):
    rs = runs.get((f, a))
    if not rs: return None
    ans = collections.Counter(x for _, x in rs).most_common(1)[0][0]
    m = statistics.median(t for t, _ in rs)
    return m if (ans in ("sat", "unsat") and m <= VT) else None
for a in arms:
    sv = [x for x in (med(f, a) for f in files) if x is not None]
    print("    %-18s %4d/%d solved  PAR2 %8.0f" % (a, len(sv), len(files),
          sum(sv) + 2 * VT * (len(files) - len(sv))))
PYEOF
done

stamp "all done"
