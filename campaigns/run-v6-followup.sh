#!/bin/bash
# Three follow-ups to the v6 campaign, cheapest and most informative first.
#
#   nohup ~/fp-abs-results/run-v6-followup.sh > ~/fp-abs-results/run-v6-followup.log 2>&1 &
#
# 1. The trail-mechanism probe (~30 min). Round count was refuted as the
#    discriminator -- Certora refines a median of 23 rounds, p90 83, the same
#    range as the quad-precision queries -- so this asks whether the trail's
#    sign tracks what the refinement is made of instead.
# 2. The seven ablations at trail off, three runs (~8 h). Settles whether the
#    value lemmas and bands really cost the layer at the reported setting.
# 3. Trail reuse on 2,000 pure bit-vector queries from sage, Sage2 and spear
#    (~4 h). The upstream default question, on more than one corpus.
set -eu
F=/home/avj/fp-abs-results/v6-followup
B=/home/avj/clones/stp/benchmark-scripts
STP=/home/avj/clones/stp/campaign-builds/fp-cegar-dc22dd12/build/stp
workers=${WORKERS:-18}
stamp() { echo "=== $1: $(date '+%F %T') ==="; }
[ -x "$STP" ] || { echo "missing binary"; exit 1; }
for f in "$F/ablate_off3.csv" "$F/pure_bv.csv"; do
  [ -e "$f" ] && [ "${FORCE:-0}" != "1" ] && { echo "$f exists; refusing. FORCE=1 to overwrite."; exit 1; }
done
echo "  stp $("$STP" --version 2>&1 | grep -oE 'SHA string [0-9a-f]{8}'), ${workers} workers"

stamp "1. trail mechanism probe, 200 binary128 queries, bv64 arm"
python3 $F/probe_trail_mechanism.py 200 "$F/probe.csv"

stamp "2. ablations at trail off, seven arms, three runs, 120 s"
cd "$B" && python3 ./run_comparison.py configs/fp_abstraction_v6_ablations_off3.yaml --no-override \
    --dir /home/avj/fp-abs-results/v6/bench-v3/queries --workers ${workers} \
    --output "$F/ablate_off3.csv" --force > "$F/ablate_off3.log" 2>&1
python3 - "$F/ablate_off3.csv" "$F/../v6/fp_abs_v6_ablate.csv" <<'PY'
import csv, collections, os, statistics, sys
def load(p):
    runs = collections.defaultdict(list)
    for r in csv.DictReader(open(p)):
        runs[(os.path.basename(r["path"]), r["solver"])].append((float(r["elapsed"]), r["answer"]))
    return runs
new, old = load(sys.argv[1]), load(sys.argv[2])
VT = 120.0
def med(runs, f, a):
    rs = runs.get((f, a))
    if not rs: return None
    ans = collections.Counter(x for _, x in rs).most_common(1)[0][0]
    m = statistics.median(t for t, _ in rs)
    return m if (ans in ("sat", "unsat") and m <= VT) else None
files = sorted({f for f, _ in new})
def sc(runs, a):
    sv = [x for x in (med(runs, f, a) for f in files) if x is not None]
    return len(sv), sum(sv) + 2 * VT * (len(files) - len(sv))
arms = ["fp-bv64", "tiers-0", "tiers-1", "tiers-2", "no-bands", "no-relational", "no-values"]
b3, b1 = sc(new, "fp-bv64"), sc(old, "fp-bv64")
print("  delta vs the full layer, trail off.   three runs (this) | one run (v6)")
for a in arms:
    n3, p3 = sc(new, a); n1, p1 = sc(old, a)
    print("    %-14s %+4d solved, PAR2 %+7.0f   |  %+4d solved, PAR2 %+7.0f" % (a, n3 - b3[0], p3 - b3[1], n1 - b1[0], p1 - b1[1]))
PY

# The three-run rows are the paper's ablation table.
python3 /home/avj/clones/fp-abs-paper/scripts/make_ablation_rows.py "$F/ablate_off3.csv" \
    /home/avj/fp-abs-results/v6/bench-v3/manifest.tsv /home/avj/clones/fp-abs-paper/data | sed 's/^/  /'

stamp "3. trail reuse on pure bit-vector work: sage 1000, Sage2 500, spear 500"
cd "$B" && python3 ./run_comparison.py configs/trail_reuse_pure_bv.yaml --no-override \
    --dir "$F/pure-bv-sample" --workers ${workers} --output "$F/pure_bv.csv" --force > "$F/pure_bv.log" 2>&1
python3 - "$F/pure_bv.csv" <<'PY'
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
for fam in ("sage__", "Sage2__", "spear__", ""):
    fs = [f for f in files if f.startswith(fam)]
    print("  %-8s %d queries" % (fam.rstrip("_") or "ALL", len(fs)))
    for a in arms:
        sv = [x for x in (med(f, a) for f in fs) if x is not None]
        print("    %-18s %4d solved  PAR2 %8.0f" % (a, len(sv), sum(sv) + 2 * VT * (len(fs) - len(sv))))
PY
stamp "all done"
