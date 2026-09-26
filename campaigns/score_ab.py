#!/usr/bin/env python3
"""Score a one-run A/B campaign per width and per arm pair.

    score_ab.py CAMPAIGN.csv MANIFEST.tsv VT [ARM ...]

Solved = conclusive answer within VT seconds; PAR2 charges 2*VT otherwise.
Arms default to every solver in the CSV, in first-seen order; the pairs
reported are consecutive arms plus first-vs-last.
"""
import csv
import os
import sys

IEEE = {"16": "binary16", "32": "binary32", "64": "binary64", "128": "binary128"}


def main():
    csv_path, manifest, vt = sys.argv[1], sys.argv[2], float(sys.argv[3])
    width = {}
    for r in csv.DictReader(open(manifest), delimiter="\t"):
        k = os.path.basename(r["file"])
        width[k] = r["maxwidth"]
        width[k[:-5] if k.endswith(".smt2") else k + ".smt2"] = r["maxwidth"]
    rows = list(csv.DictReader(open(csv_path)))
    arms = sys.argv[4:] or list(dict.fromkeys(r["solver"] for r in rows))
    data = {(os.path.basename(r["path"]), r["solver"]): (float(r["elapsed"]), r["answer"]) for r in rows}
    files = sorted({f for f, _ in data})

    def solved(f, a):
        t, ans = data.get((f, a), (None, ""))
        return ans in ("sat", "unsat") and t <= vt

    print("%s: %d queries, %d without a width, VT=%g s" % (os.path.basename(csv_path), len(files),
                                                         sum(1 for f in files if f not in width), vt))
    print("%-10s %5s | %s" % ("width", "n", " | ".join("%-28s" % a for a in arms)))
    for w in sorted({width.get(f, "?") for f in files}, key=lambda x: int(x) if x.isdigit() else 999) + ["all"]:
        fs = [f for f in files if w == "all" or width.get(f, "?") == w]
        common = [f for f in fs if all(solved(f, a) for a in arms)]
        cells = []
        for a in arms:
            s = sum(1 for f in fs if solved(f, a))
            par2 = sum(data[(f, a)][0] if solved(f, a) else 2 * vt for f in fs)
            c = sum(data[(f, a)][0] for f in common)
            cells.append("%4d slv %7.0f par2 %6.0f cmn" % (s, par2, c))
        print("%-10s %5d | %s" % (IEEE.get(w, w), len(fs), " | ".join(cells)))
    contra = [f for f in files if len({data[(f, a)][1] for a in arms if data[(f, a)][1] in ("sat", "unsat")}) > 1]
    print("answer contradictions: %d" % len(contra))
    for f in contra[:10]:
        print("   ", f, {a: data[(f, a)][1] for a in arms})
    pairs = list(zip(arms, arms[1:]))
    if len(arms) > 2:
        pairs.append((arms[0], arms[-1]))
    for a, b in pairs:
        lost = [f for f in files if solved(f, a) and not solved(f, b)]
        gained = [f for f in files if solved(f, b) and not solved(f, a)]
        both = [f for f in files if solved(f, a) and solved(f, b)]
        slow = sum(1 for f in both if data[(f, b)][0] > 2 * data[(f, a)][0] and data[(f, b)][0] > 2)
        fast = sum(1 for f in both if data[(f, a)][0] > 2 * data[(f, b)][0] and data[(f, a)][0] > 2)
        print("%s -> %s: solved by first only %d, by second only %d; on %d common: second >2x slower %d, "
              ">2x faster %d, time %.0f -> %.0f s" % (a, b, len(lost), len(gained), len(both), slow, fast,
                                                       sum(data[(f, a)][0] for f in both),
                                                       sum(data[(f, b)][0] for f in both)))
        for f in lost[:15]:
            print("     first-only:  %s (%.1f s)" % (f, data[(f, a)][0]))
        for f in gained[:15]:
            print("     second-only: %s (%.1f s)" % (f, data[(f, b)][0]))


if __name__ == "__main__":
    main()
