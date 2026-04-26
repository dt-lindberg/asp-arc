"""Render results/rewriter_progress.csv as a step plot.

x: cumulative number of rules.
y: cumulative number of programs fixed (out of N broken in the corpus).
Each step labelled with the rule's short description and `+Δ`.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "results" / "rewriter_progress.csv"
PNG = ROOT / "results" / "rewriter_progress.png"


def main() -> None:
    if not CSV.exists():
        print(f"No CSV at {CSV}", file=sys.stderr)
        sys.exit(1)
    rows = list(csv.DictReader(CSV.open()))
    if not rows:
        print("CSV empty", file=sys.stderr)
        sys.exit(1)

    xs = [int(r["rule_index"]) for r in rows]
    ys = [int(r["total_fixed"]) for r in rows]
    deltas = [int(r["delta_fixed"]) for r in rows]
    labels = [r["short_description"] for r in rows]
    n_total = int(rows[-1]["n_total"])
    final = ys[-1]

    fig, ax = plt.subplots(figsize=(11, 6))
    ax.step(xs, ys, where="post", marker="o", linewidth=2)
    for x, y, lab, dy in zip(xs, ys, labels, deltas):
        ann = f"{lab}\n(+{dy})" if dy >= 0 else f"{lab}\n({dy})"
        ax.annotate(
            ann,
            xy=(x, y),
            xytext=(6, 8),
            textcoords="offset points",
            fontsize=9,
            ha="left",
        )

    ax.set_xlabel("Cumulative number of rewrite rules")
    ax.set_ylabel("Programs fixed (out of broken corpus)")
    ax.set_title(
        f"Deterministic rewriter progress — {final}/{n_total} fixed "
        f"({final / n_total * 100:.1f}% of broken programs)"
    )
    ax.grid(True, axis="y", alpha=0.3)
    # Zoom to the data range so small-fix curves stay legible; the
    # corpus-size context lives in the title.
    y_top = max(max(ys) * 1.5 + 2, 10)
    ax.set_ylim(0, y_top)
    ax.set_xlim(-0.5, max(xs) + 1.5)
    # Right-axis ticks expressing the same y in % of corpus.
    ax2 = ax.twinx()
    ax2.set_ylim(0, y_top / n_total * 100)
    ax2.set_ylabel("% of broken corpus fixed")

    fig.tight_layout()
    fig.savefig(PNG, dpi=130)
    print(f"Wrote {PNG}")


if __name__ == "__main__":
    main()
