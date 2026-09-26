#!/bin/bash
# The trail-reuse ablation: the two abstraction arms of the v5 campaign with
# --refinement-trail-reuse at its default, against the same corpus and binary.
#
#   nohup ~/fp-abs-results/run-v5-trail.sh > ~/fp-abs-results/run-v5-trail.log 2>&1 &
set -eu
R=${OUT:-/home/avj/fp-abs-results/v5}
B=/home/avj/clones/stp/benchmark-scripts
STP=/home/avj/clones/stp/campaign-builds/fp-cegar-snapshot/build/stp
CFG=configs/fp_abstraction_v5_trail.yaml
workers=${WORKERS:-18}
Q=$R/bench-v3/queries
MAN=$R/bench-v3/manifest.tsv
OUTCSV=$R/fp_abs_trail_on.csv

stamp() { echo "=== $1: $(date '+%F %T') ==="; }
stamp "preflight"
[ -x "$STP" ] || { echo "missing binary: $STP"; exit 1; }
[ -d "$Q" ] || { echo "missing corpus: $Q"; exit 1; }
[ -f "$R/fp_abs_vs_bwz.csv" ] || { echo "the main pass has not run; nothing to compare against"; exit 1; }
if [ -e "$OUTCSV" ] && [ "${FORCE:-0}" != "1" ]; then
    echo "$OUTCSV exists; refusing to overwrite. Set FORCE=1 if that is what you want."
    exit 1
fi
echo "  stp $("$STP" --version 2>&1 | grep -oE 'SHA string [0-9a-f]{8}'), $(ls "$Q" | wc -l) queries, ${workers} workers"

stamp "ablation: two arms at the default trail setting, three runs, 60 s + revalidation"
cd "$B" && python3 ./run_comparison.py "$CFG" --no-override --dir "$Q" \
    --workers ${workers} --output "$OUTCSV" --force > "$R/trail.log" 2>&1
tail -3 "$R/trail.log"

stamp "the comparison the ablation exists to make"
python3 - "$R/fp_abs_vs_bwz.csv" "$OUTCSV" "$MAN" <<'PY'
import csv, collections, os, statistics, sys
def load(p):
    runs = collections.defaultdict(list)
    for r in csv.DictReader(open(p)):
        runs[(os.path.basename(r["path"]), r["solver"])].append((float(r["elapsed"]), r["answer"]))
    return runs
off, on = load(sys.argv[1]), load(sys.argv[2])
man = {r["file"]: int(r["maxwidth"]) for r in csv.DictReader(open(sys.argv[3]), delimiter="\t")}
VT = 120.0
def med(runs, f, a):
    rs = runs.get((f, a))
    if not rs: return None
    ans = collections.Counter(x for _, x in rs).most_common(1)[0][0]
    m = statistics.median(t for t, _ in rs)
    return m if (ans in ("sat", "unsat") and m <= VT) else None
files = sorted({f for f, _ in off})
def stats(runs, fs, arm):
    s = [med(runs, f, arm) for f in fs]
    solved = [x for x in s if x is not None]
    return len(solved), sum(solved) + 2 * VT * (len(fs) - len(solved))
print("  %-10s %-28s %-28s" % ("width", "trail off (main pass)", "trail on (this run)"))
for w in (16, 32, 64, 128, None):
    fs = [f for f in files if (man.get(f) == w if w else True)]
    a1, p1 = stats(off, fs, "stp-bv64");          a2, p2 = stats(off, fs, "stp-fp-bv64")
    b1, q1 = stats(on,  fs, "stp-bv64-trail");    b2, q2 = stats(on,  fs, "stp-fp-bv64-trail")
    print("  %-10s FP %+d  (%d vs %d)%s FP %+d  (%d vs %d)" % (
        "binary%d" % w if w else "all", a2 - a1, a2, a1, " " * 8, b2 - b1, b2, b1))
    print("  %-10s   PAR2 %.0f vs %.0f%s  PAR2 %.0f vs %.0f" % ("", p2, p1, " " * 10, q2, q1))
PY
stamp "all done"
