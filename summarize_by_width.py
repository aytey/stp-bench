#!/usr/bin/env python3
"""Score a campaign per floating-point width, and per arm.

    summarize_by_width.py CAMPAIGN.csv MANIFEST.tsv [REVALIDATION.csv] [--vt 120]

A campaign CSV says what each arm answered for each query and how long it took;
the corpus manifest says how wide each query's widest float is. Reporting only
the total hides the thing the corpus was built to show, because the cost of an
exact floating-point circuit grows with the width while the abstraction's rules
do not -- an arm can be ahead overall and behind at binary16.

The reduction is the one the campaigns use: keep every repetition, take the
median elapsed time and a strict majority for the status, count a query solved
when that majority is conclusive and the median is within the virtual timeout,
and charge PAR2 twice the timeout for everything else. A revalidation pass, if
given, replaces every repetition of the query and arm it covers. An UNKNOWN, a
crash or a tie is unsolved: no answer is not a fast answer.
"""
import collections
import csv
import statistics
import sys
from pathlib import Path

CONCLUSIVE = {"sat", "unsat"}


def read_runs(path):
    runs = collections.defaultdict(list)
    with open(path, newline="") as stream:
        for row in csv.DictReader(stream):
            runs[Path(row["path"]).name, row["solver"]].append(
                (float(row["elapsed"]), row["answer"]))
    return runs


def reduce_one(values):
    """(median time, majority answer) over a query's repetitions."""
    answers = collections.Counter(a for _, a in values)
    answer, count = answers.most_common(1)[0]
    if 2 * count <= sum(answers.values()):
        answer = "mixed"
    return statistics.median(t for t, _ in values), answer


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    vt = 120.0
    for a in sys.argv[1:]:
        if a.startswith("--vt"):
            vt = float(a.split("=", 1)[1])
    if len(args) < 2:
        sys.exit(__doc__)
    campaign, manifest = args[0], args[1]

    runs = read_runs(campaign)
    if len(args) > 2:
        runs.update(read_runs(args[2]))

    width = {}
    with open(manifest, newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            width[row["file"]] = int(row["maxwidth"])

    arms = sorted({arm for _, arm in runs})
    seen = collections.defaultdict(set)          # width -> files
    solved = collections.defaultdict(int)        # (width, arm) -> n
    par2 = collections.defaultdict(float)
    missing = 0

    for (name, arm), values in runs.items():
        if name not in width:
            missing += 1
            continue
        w = width[name]
        seen[w].add(name)
        elapsed, answer = reduce_one(values)
        if answer in CONCLUSIVE and elapsed <= vt:
            solved[w, arm] += 1
            par2[w, arm] += elapsed
        else:
            par2[w, arm] += 2 * vt

    if missing:
        print(f"warning: {missing} query/arm rows are not in the manifest",
              file=sys.stderr)

    label = {0: "no float"}
    print(f"virtual timeout {vt:g}s; PAR2 charges {2 * vt:g}s for unsolved\n")
    head = f"{'width':>8}  {'queries':>7}  " + "  ".join(f"{a:>22}" for a in arms)
    print(head)
    print(f"{'':>8}  {'':>7}  " + "  ".join(f"{'solved':>10}{'PAR2':>12}" for _ in arms))
    for w in sorted(seen):
        cells = "  ".join(f"{solved[w, a]:>10}{par2[w, a]:>12,.0f}" for a in arms)
        print(f"{label.get(w, 'binary' + str(w)):>8}  {len(seen[w]):>7}  {cells}")
    total_n = sum(len(v) for v in seen.values())
    cells = "  ".join(
        f"{sum(solved[w, a] for w in seen):>10}"
        f"{sum(par2[w, a] for w in seen):>12,.0f}" for a in arms)
    print(f"{'total':>8}  {total_n:>7}  {cells}")


if __name__ == "__main__":
    main()
