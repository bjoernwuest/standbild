"""``standbild preview``: Diagramm der Änderungswerte eines Pakets zum Einstellen der Schwellen (7.7)."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

# Kategorische Farben, auf Farbfehlsichtigkeit geprüft; Schwellen tragen die Farbe ihrer Messreihe.
BLUE, ORANGE, INK, MUTED, GRID, SURFACE = "#2a78d6", "#eb6834", "#1f1f1e", "#6b6a63", "#e4e3dd", "#fcfcfb"
FLOOR = 1e-4  # untere Grenze der logarithmischen Achse


def render(package: Path, out: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    with zipfile.ZipFile(package) as z:
        rows = list(csv.DictReader(io.TextIOWrapper(z.open("log/detection_log.csv"), encoding="utf-8")))
        shots = json.loads(z.read("log/frames.json"))
        meta = json.loads(z.read("meta.json"))
    opts = meta["options"]
    t = [float(r["t"]) for r in rows]
    end = t[-1] if t else 1.0

    fig, ax = plt.subplots(figsize=(min(40.0, max(11.0, end / 25)), 4.8), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    series = (("global_ratio", "threshold", BLUE), ("largest_region", "min_local_change", ORANGE))
    for column, limit, color in series:
        ax.plot(t, [max(float(r[column]), FLOOR) for r in rows], color=color, linewidth=1.3, label=column)
        ax.axhline(opts[limit], color=color, linewidth=1.0, linestyle=(0, (5, 3)))
        ax.annotate(
            f" {limit} = {opts[limit]:g}", (end, opts[limit]), color=INK, fontsize=8, va="bottom", ha="right",
            xytext=(-2, 2), textcoords="offset points",
        )  # fmt: skip
    for i, s in enumerate(shots):
        label = "Screenshot gespeichert" if i == 0 else None
        ax.axvline(s["t"], color=MUTED, linewidth=0.8, alpha=0.55, zorder=1, label=label)
    ax.set_yscale("log")
    ax.set_ylim(FLOOR, 1.3)
    ax.set_xlim(0, end)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{int(x) // 60}:{int(x) % 60:02d}"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f"{y * 100:g} %"))
    ax.set_xlabel("Zeit im Video (min:s)", color=MUTED)
    ax.set_ylabel("Anteil der Bildfläche (log)", color=MUTED)
    ax.set_title(
        f"{meta.get('title', package.stem)} – {len(shots)} Screenshots, Profil {opts['profile']}",
        loc="left", color=INK, fontsize=11,
    )  # fmt: skip
    ax.grid(True, which="major", color=GRID, linewidth=0.6)
    ax.tick_params(colors=MUTED, labelsize=8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.legend(loc="upper left", bbox_to_anchor=(0, -0.16), ncols=3, frameon=False, fontsize=8, labelcolor=INK)
    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    return out
