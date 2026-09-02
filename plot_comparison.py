#!/usr/bin/env python3
"""
Generate cactus plots (Fig. 4 style) and scatter plots (Fig. 5 style)
comparing two solvers from a comparison run.

Produces plots at two virtual timeouts (24s and 2m), matching the score
tables from summarize_comparison.py:
  - 6 cactus + 6 scatter per virtual timeout = 24 plots
  - stp_comparison.html referencing all plots

Usage:
    python3 plot_comparison.py build_comparison.csv build_comparison_revalidation.csv
"""

import argparse
import html
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from benchlib import load_combined, extract_logic, solvers_for_csv


# ── Colours & markers per logic (matching Fig. 5 style) ──────────────────

LOGIC_STYLE = {
    "QF_ABV":   {"color": "#1f77b4", "marker": "^"},
    "QF_ABVFP": {"color": "#ff7f0e", "marker": "o"},
    "QF_BV":    {"color": "#d62728", "marker": "x"},
    "QF_BVFP":  {"color": "#9467bd", "marker": "s"},
    "QF_FP":    {"color": "#2ca02c", "marker": "D"},
}

_EXTRA_COLORS = ["#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]
_EXTRA_MARKERS = ["v", "<", ">", "p", "*"]


def style_for(logic, idx=0):
    if logic in LOGIC_STYLE:
        return LOGIC_STYLE[logic]
    i = idx % len(_EXTRA_COLORS)
    return {"color": _EXTRA_COLORS[i], "marker": _EXTRA_MARKERS[i]}


def vto_label(vto):
    """Human-readable label for a virtual timeout value."""
    if vto < 60:
        return f"{vto:.0f}s"
    return f"{vto / 60:.0f}m"


def vto_slug(vto):
    """Filename-safe slug for a virtual timeout value."""
    if vto < 60:
        return f"{vto:.0f}s"
    return f"{vto / 60:.0f}m"


# ── Data preparation ─────────────────────────────────────────────────────

def resolve_pair(parser, csv_path, solver_a, solver_b):
    """Pick the two solvers to compare, honouring whichever was given.

    Defaults come from the run manifest, so the baseline the config listed
    first stays the baseline here.
    """
    names = solvers_for_csv(csv_path)
    for name in (solver_a, solver_b):
        if name is not None and name not in names:
            parser.error(f"no results for solver {name!r} in {csv_path} "
                         f"(found: {', '.join(names) or 'none'})")
    if solver_a is None:
        solver_a = next((n for n in names if n != solver_b), None)
    if solver_b is None:
        solver_b = next((n for n in names if n != solver_a), None)
    if not solver_a or not solver_b:
        parser.error(f"{csv_path} holds fewer than two solvers "
                     f"({', '.join(names) or 'none'}); pass --solver-a/--solver-b")
    return solver_a, solver_b

def prepare_data(main_csv, reval_csv, solver_a, solver_b):
    """Load CSVs and group by logic.

    Returns by_logic: logic -> list of
    (solver_a_time, solver_b_time, solver_a_answer, solver_b_answer).
    """
    data = load_combined(main_csv, reval_csv)

    files_m = {p: v for (p, s), v in data.items() if s == solver_a}
    files_i = {p: v for (p, s), v in data.items() if s == solver_b}
    common = sorted(set(files_m) & set(files_i))

    by_logic = {}
    for p in common:
        m = files_m[p]
        i = files_i[p]
        logic = extract_logic(p)
        by_logic.setdefault(logic, []).append(
            (m["elapsed"], i["elapsed"], m["answer"], i["answer"])
        )
    return by_logic


def is_solved(elapsed, answer, vto):
    """True if the instance counts as solved under a virtual timeout."""
    return answer in ("sat", "unsat") and elapsed < vto


# ── Figure 4: cactus plot ────────────────────────────────────────────────

def cactus_plot(points_a, points_b, labels, vto, title, out_path):
    """Draw a cactus (cumulative solved) plot.

    points_a / points_b: list of (elapsed, answer) tuples.
    labels: (solver_a_name, solver_b_name).
    """
    fig, ax = plt.subplots(figsize=(6, 4.5))

    for label, points, color, ls in [
        (labels[0], points_a, "#d62728", "-"),
        (labels[1], points_b, "#1f77b4", "--"),
    ]:
        solved_times = sorted(
            t for t, a in points if is_solved(t, a, vto)
        )
        xs = [0.0] + list(solved_times) + [vto]
        ys = [0]   + list(range(1, len(solved_times) + 1)) + [len(solved_times)]
        ax.step(xs, ys, where="post", label=label, color=color, linestyle=ls,
                linewidth=1.5)

    ax.set_xlabel("Running time $t$ [s]")
    ax.set_ylabel(r"# instances solved in $\leq t$")
    ax.set_xlim(0, vto)
    ax.set_title(title, fontsize=11)
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"  {out_path}")


# ── Figure 5: scatter plot with timeout border ───────────────────────────

def scatter_plot(by_logic_points, labels, vto, title, out_path):
    """Draw a log-log scatter plot with gray timeout border.

    by_logic_points: dict  logic -> list of (a_t, b_t, a_ans, b_ans).
    labels: (solver_a_name, solver_b_name), used for the axis labels.
    Instances that are unsolved under the virtual timeout are placed
    firmly inside the gray margin at 1.3x the timeout.
    """
    fig, ax = plt.subplots(figsize=(5.5, 5.5))

    all_times = []
    for pts in by_logic_points.values():
        for mt, it, ma, ia in pts:
            all_times.extend([mt, it])
    if not all_times:
        plt.close(fig)
        return

    lo = max(min(all_times) * 0.5, 0.05)
    hi = vto * 1.8
    to_place = vto * 1.35  # geometric center of gray band on log-scale

    # ── Gray timeout border ──────────────────────────────────────────
    ax.axhspan(vto, hi, color="#d0d0d0", alpha=0.5, zorder=0)
    ax.axvspan(vto, hi, color="#d0d0d0", alpha=0.5, zorder=0)
    ax.axhline(vto, color="#999999", linewidth=0.7, linestyle="-", zorder=1)
    ax.axvline(vto, color="#999999", linewidth=0.7, linestyle="-", zorder=1)

    # ── Diagonal reference lines ─────────────────────────────────────
    diag = np.logspace(np.log10(lo), np.log10(hi), 200)
    ax.plot(diag, diag, color="#aaaaaa", linewidth=1, linestyle="--",
            zorder=2, label="equal")
    ax.plot(diag, 10 * diag, color="#cccccc", linewidth=0.6, linestyle=":",
            zorder=2, label="10x")
    ax.plot(diag, 0.1 * diag, color="#cccccc", linewidth=0.6, linestyle=":",
            zorder=2)

    # ── Scatter points per logic ─────────────────────────────────────
    extra_idx = 0
    for logic in sorted(by_logic_points):
        pts = by_logic_points[logic]
        if not pts:
            continue
        s = style_for(logic, extra_idx)
        if logic not in LOGIC_STYLE:
            extra_idx += 1

        xs, ys = [], []
        for mt, it, ma, ia in pts:
            m_ok = is_solved(mt, ma, vto)
            i_ok = is_solved(it, ia, vto)
            xs.append(mt if m_ok else to_place)
            ys.append(it if i_ok else to_place)

        edge = "none" if s["marker"] not in ("x", "+", "1", "2", "3", "4") else s["color"]
        ax.scatter(xs, ys, c=s["color"], marker=s["marker"],
                   s=18, alpha=0.7, linewidths=0.4, edgecolors=edge,
                   label=logic, zorder=5)

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xlabel(f"Solve time of {labels[0]} [s]")
    ax.set_ylabel(f"Solve time of {labels[1]} [s]")
    ax.set_title(title, fontsize=11)
    ax.set_aspect("equal")
    ax.legend(loc="upper left", fontsize=7, markerscale=1.5,
              framealpha=0.9, handletextpad=0.3)
    ax.grid(True, which="major", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"  {out_path}")


# ── HTML report ──────────────────────────────────────────────────────────

def write_html(outdir, logics, ext, main_csv, reval_csv, virtual_timeouts, labels):
    """Write stp_comparison.html referencing all generated plots."""
    if ext == "pdf":
        img_tag = '<embed src="{src}" type="application/pdf" width="100%" height="600px">'
    else:
        img_tag = '<img src="{src}" alt="{alt}">'

    def img(filename, alt=""):
        return img_tag.format(src=filename, alt=html.escape(alt))

    logic_list = ", ".join(logics)
    sections = []

    for vto in virtual_timeouts:
        slug = vto_slug(vto)
        label = vto_label(vto)
        rows = []

        # All theories row
        rows.append(f"""
      <tr>
        <td>All theories</td>
        <td>{img(f"cactus_all_{slug}.{ext}", f"Cactus — all theories ({label})")}</td>
        <td>{img(f"scatter_all_{slug}.{ext}", f"Scatter — all theories ({label})")}</td>
      </tr>""")

        for logic in logics:
            rows.append(f"""
      <tr>
        <td>{html.escape(logic)}</td>
        <td>{img(f"cactus_{logic}_{slug}.{ext}", f"Cactus — {logic} ({label})")}</td>
        <td>{img(f"scatter_{logic}_{slug}.{ext}", f"Scatter — {logic} ({label})")}</td>
      </tr>""")

        sections.append(f"""
<h2>Virtual timeout: {label}</h2>
<table>
  <thead>
    <tr>
      <th>Theory</th>
      <th>Cactus plot (Fig.&nbsp;4 style)</th>
      <th>Scatter plot (Fig.&nbsp;5 style)</th>
    </tr>
  </thead>
  <tbody>{"".join(rows)}
  </tbody>
</table>""")

    html_content = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(labels[0])} vs {html.escape(labels[1])}</title>
<style>
  body {{ font-family: sans-serif; margin: 2em; background: #fafafa; }}
  h1 {{ color: #333; }}
  h2 {{ color: #555; margin-top: 2em; }}
  .meta {{ color: #666; font-size: 0.9em; margin-bottom: 1.5em; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 2em; }}
  th, td {{ border: 1px solid #ccc; padding: 0.6em; text-align: center;
            vertical-align: top; }}
  th {{ background: #eee; }}
  td:first-child {{ font-weight: bold; white-space: nowrap; width: 8em; }}
  img, embed {{ max-width: 100%; height: auto; }}
</style>
</head>
<body>
<h1>{html.escape(labels[0])} vs {html.escape(labels[1])}</h1>
<p class="meta">
  Main CSV: <code>{html.escape(os.path.basename(main_csv))}</code><br>
  Revalidation CSV: <code>{html.escape(os.path.basename(reval_csv))}</code><br>
  Theories: {html.escape(logic_list)}
</p>
{"".join(sections)}
</body>
</html>
"""
    path = os.path.join(outdir, "stp_comparison.html")
    with open(path, "w") as f:
        f.write(html_content)
    print(f"  {path}")


# ── Main ─────────────────────────────────────────────────────────────────

VIRTUAL_TIMEOUTS = [24, 120]


def main():
    parser = argparse.ArgumentParser(
        description="Generate cactus + scatter comparison plots."
    )
    parser.add_argument("main_csv")
    parser.add_argument("reval_csv")
    parser.add_argument("--outdir", default=".")
    parser.add_argument("--solver-a", default=None,
                        help="Baseline solver (default: first one in the CSV)")
    parser.add_argument("--solver-b", default=None,
                        help="Solver to plot against it (default: second one in the CSV)")
    parser.add_argument("--format", default="svg", choices=["pdf", "svg", "png"],
                        help="Output image format (default: svg)")
    args = parser.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    ext = args.format

    solver_a, solver_b = resolve_pair(parser, args.main_csv,
                                      args.solver_a, args.solver_b)
    labels = (solver_a, solver_b)
    print(f"Comparing: {solver_a} vs {solver_b}")

    by_logic = prepare_data(args.main_csv, args.reval_csv, solver_a, solver_b)
    logics = sorted(by_logic)
    print(f"Logics: {', '.join(logics)}")

    total = 0
    for vto in VIRTUAL_TIMEOUTS:
        slug = vto_slug(vto)
        label = vto_label(vto)
        print(f"\n── Virtual timeout: {label} ──")

        # Cactus plots
        pair = f"{solver_a} vs {solver_b}"
        all_a = [(at, aa) for pts in by_logic.values() for at, _, aa, _ in pts]
        all_b = [(bt, ba) for pts in by_logic.values() for _, bt, _, ba in pts]
        cactus_plot(all_a, all_b, labels, vto,
                    f"All theories ({label}) — {pair}",
                    os.path.join(args.outdir, f"cactus_all_{slug}.{ext}"))
        total += 1

        for logic in logics:
            pts = by_logic[logic]
            a_pts = [(at, aa) for at, _, aa, _ in pts]
            b_pts = [(bt, ba) for _, bt, _, ba in pts]
            cactus_plot(a_pts, b_pts, labels, vto,
                        f"{logic} ({label}) — {pair}",
                        os.path.join(args.outdir, f"cactus_{logic}_{slug}.{ext}"))
            total += 1

        # Scatter plots
        scatter_plot(by_logic, labels, vto,
                     f"All theories ({label}) — {pair}",
                     os.path.join(args.outdir, f"scatter_all_{slug}.{ext}"))
        total += 1

        for logic in logics:
            scatter_plot({logic: by_logic[logic]}, labels, vto,
                         f"{logic} ({label}) — {pair}",
                         os.path.join(args.outdir, f"scatter_{logic}_{slug}.{ext}"))
            total += 1

    # HTML report
    write_html(args.outdir, logics, ext, args.main_csv, args.reval_csv,
               VIRTUAL_TIMEOUTS, labels)

    print(f"\nDone — {total} plots + HTML report.")


if __name__ == "__main__":
    main()
