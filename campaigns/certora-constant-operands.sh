#!/bin/bash
# Whether a record for a wide operation with a constant operand pays at all,
# on QF_UFBV/20241113-Certora -- the public corpus upstream's own
# value-blocking cap for such a record was built on. Four arms: upstream at
# the commit the branch sits on, the branch with its defaults (which should
# be the same thing on a query with no floating point in it), the branch
# declining such records, and the branch with the cap lifted.
#
#   nohup ~/fp-abs-results/certora-constant-operands.sh > ~/fp-abs-results/certora-constant-operands.log 2>&1 &
#   RUNS=3 nohup ~/fp-abs-results/certora-constant-operands.sh > ... &
set -eu
R=${OUT:-/home/avj/fp-abs-results/certora}
B=/home/avj/clones/stp/benchmark-scripts
CFG=configs/bv_constant_operands_certora.yaml
Q=${CORPUS:-/mnt/baranem/smt2_problems/non-incremental/QF_UFBV/20241113-Certora}
UP=/home/avj/clones/stp/campaign-builds/upstream-ad383269/build/stp
BR=/home/avj/clones/stp/campaign-builds/fp-cegar-85903b6fce13/build/stp
RUNS=${RUNS:-1}
TIMEOUT=${TIMEOUT:-60}
W=${WORKERS:-24}
mkdir -p "$R"
stamp() { echo "=== $1: $(date '+%F %T') ==="; }

stamp "preflight"
[ -d "$Q" ] || { echo "missing corpus: $Q"; exit 1; }
for bin in "$UP" "$BR"; do [ -x "$bin" ] || { echo "missing binary: $bin"; exit 1; }; done
"$BR" --help 2>&1 | grep -q -- --bv-term-abstraction-constant-operands || {
  echo "the branch binary does not have --bv-term-abstraction-constant-operands"; exit 1; }
"$UP" --help 2>&1 | grep -q -- --bv-term-abstraction-constant-operands && {
  echo "the upstream binary HAS the knob -- it is not upstream"; exit 1; }
# The two must be the same build otherwise, or the upstream arm measures the
# compiler rather than the tree.
diff <("$UP" --version | sed -E 's/SHA string [0-9a-f]+//; s/compilation date time.*//') \
     <("$BR" --version | sed -E 's/SHA string [0-9a-f]+//; s/compilation date time.*//') > /dev/null || {
  echo "the two binaries were built with different options"; exit 1; }
echo "  upstream $("$UP" --version | grep -oE 'SHA string [0-9a-f]{8}'), branch $("$BR" --version | grep -oE 'SHA string [0-9a-f]{8}'), $(find "$Q" -name '*.smt2' | wc -l) queries, $RUNS run(s) at ${TIMEOUT}s on $W workers"

stamp "manifest: the widest bit-vector each query declares"
python3 - "$Q" "$R/manifest.tsv" <<'PY'
import os, re, sys
root, out = sys.argv[1], sys.argv[2]
width = re.compile(r"\(_ BitVec (\d+)\)")
with open(out, "w") as fh:
    fh.write("file\tmaxwidth\tbytes\n")
    for dirpath, _, names in os.walk(root):
        for name in sorted(names):
            if not name.endswith(".smt2"):
                continue
            path = os.path.join(dirpath, name)
            widest = 0
            with open(path, errors="replace") as q:
                for line in q:
                    for m in width.finditer(line):
                        widest = max(widest, int(m.group(1)))
            fh.write("%s\t%d\t%d\n" % (name, widest, os.path.getsize(path)))
PY
awk -F'\t' 'NR>1 {n[$2]++} END {for (w in n) printf "  %s-bit: %d\n", w, n[w]}' "$R/manifest.tsv" | sort -t: -k1 -n

stamp "campaign: 4 arms, $RUNS run(s), ${TIMEOUT}s"
cd "$B" && python3 ./run_comparison.py "$CFG" --no-override --dir "$Q" \
    --timeout "$TIMEOUT" --runs "$RUNS" --no-revalidate --workers "$W" \
    --output "$R/constant_operands.csv" --force > "$R/constant_operands.log" 2>&1
tail -3 "$R/constant_operands.log"

stamp "scores"
python3 /home/avj/fp-abs-results/score_ab.py "$R/constant_operands.csv" "$R/manifest.tsv" "$TIMEOUT" \
    upstream branch-default no-constant-records uncapped | tee "$R/constant_operands.score.txt"

stamp "the branch's own accounting, on the queries the knob changes"
python3 - "$R/constant_operands.csv" "$TIMEOUT" <<'PY'
import collections, csv, os, statistics, sys
rows = list(csv.DictReader(open(sys.argv[1])))
vt = float(sys.argv[2])
runs = collections.defaultdict(list)
for r in rows:
    runs[(os.path.basename(r["path"]), r["solver"])].append((float(r["elapsed"]), r["answer"]))
def score(f, a):
    rs = runs[(f, a)]
    answer = collections.Counter(x for _, x in rs).most_common(1)[0][0]
    median = statistics.median(t for t, _ in rs)
    return (answer if answer in ("sat", "unsat") and median <= vt else None), median
files = sorted({f for f, _ in runs})
base, knob = "branch-default", "no-constant-records"
gained = [f for f in files if score(f, knob)[0] and not score(f, base)[0]]
lost = [f for f in files if score(f, base)[0] and not score(f, knob)[0]]
print("declining the records: %d queries gained, %d lost" % (len(gained), len(lost)))
for tag, group in (("gained", gained), ("lost", lost)):
    for f in group[:20]:
        print("  %-7s %-14s %s %.1fs -> %s %.1fs" % (tag, f, score(f, base)[0] or "T/O",
              score(f, base)[1], score(f, knob)[0] or "T/O", score(f, knob)[1]))
both = [f for f in files if score(f, base)[0] and score(f, knob)[0]]
faster = [f for f in both if score(f, base)[1] > 2 * score(f, knob)[1] and score(f, base)[1] > 2]
slower = [f for f in both if score(f, knob)[1] > 2 * score(f, base)[1] and score(f, knob)[1] > 2]
print("on the %d both solve: %d more than twice as fast declined, %d more than twice as slow"
      % (len(both), len(faster), len(slower)))
print("upstream and the branch's default disagree on %d queries (expect 0: nothing the branch"
      " adds is reachable without a floating-point operation)"
      % sum(1 for f in files if score(f, "upstream")[0] != score(f, base)[0]))
PY
stamp "all done"
