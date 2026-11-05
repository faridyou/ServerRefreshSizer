"""matplotlib charts -> PNG bytes (embedded base64 in HTML/PDF and as images in Excel).

Colors: validated reference palette, first three categorical slots (blue, orange, aqua: safe all-pairs);
recessive grid and axes; text in text ink, never the series color; direct labels on bars."""

from __future__ import annotations

import base64
import io
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e4e3df"
S1, S2, S3 = "#2a78d6", "#eb6834", "#1baf7a"
NEUTRAL = "#9a9890"


def _fig(w: float = 6.4, h: float = 3.2) -> tuple[Any, Any]:
    fig, ax = plt.subplots(figsize=(w, h), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    return fig, ax


def _png(fig: Any) -> bytes:
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", facecolor=SURFACE)
    plt.close(fig)
    return buf.getvalue()


def b64(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def host_comparison(labels: list[str], values: list[int], title: str) -> bytes:
    fig, ax = _fig(5.6, 2.8)
    colors = [S1, S2, S3][: len(values)]
    bars = ax.bar(labels, values, color=colors, width=0.55, edgecolor=SURFACE, linewidth=2)
    for b, v in zip(bars, values, strict=True):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height(), f"{v}", ha="center", va="bottom", fontsize=9,
                color=INK)
    ax.set_title(title, fontsize=10, color=INK, loc="left")
    ax.set_ylabel("hosts", fontsize=8, color=INK2)
    ax.set_ylim(0, max(values + [1]) * 1.2)
    return _png(fig)


def pareto(all_pts: list[tuple[int, float]], front: list[tuple[int, float, str]], rec: tuple[int, float] | None,
           title: str) -> bytes:
    fig, ax = _fig(7.6, 3.4)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    if all_pts:
        ax.scatter([p[0] for p in all_pts], [p[1] / 1000 for p in all_pts], s=14, color=NEUTRAL, alpha=0.6,
                   linewidths=0, label="feasible configs")
    if front:
        xs = [p[0] for p in front]
        ys = [p[1] / 1000 for p in front]
        ax.plot(xs, ys, color=S1, linewidth=2, marker="o", markersize=5, markeredgecolor=SURFACE,
                markeredgewidth=2, label="Pareto front")
        for x, y, lab in front[:6]:
            ax.annotate(lab, (x, y / 1000), textcoords="offset points", xytext=(5, 5), fontsize=7, color=INK2)
    if rec:
        ax.scatter([rec[0]], [rec[1] / 1000], s=70, color=S2, edgecolors=SURFACE, linewidths=2, zorder=5,
                   label="recommended")
    ax.xaxis.get_major_locator().set_params(integer=True)
    ax.set_xlabel("hosts", fontsize=8, color=INK2)
    ax.set_ylabel("TCO (k USD)", fontsize=8, color=INK2)
    ax.set_title(title, fontsize=10, color=INK, loc="left")
    ax.legend(fontsize=7, frameon=False, labelcolor=INK2, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    return _png(fig)


def histogram(hist: dict[int, int], p50: int, p90: int, title: str) -> bytes:
    fig, ax = _fig(5.6, 2.8)
    ks = sorted(hist)
    ax.bar([str(k) for k in ks], [hist[k] for k in ks], color=S1, width=0.6, edgecolor=SURFACE, linewidth=2)
    for k in ks:
        tag = " P90" if k == p90 else (" P50" if k == p50 else "")
        if tag:
            ax.text(ks.index(k), hist[k], tag.strip(), ha="center", va="bottom", fontsize=8, color=INK)
    ax.set_xlabel("hosts", fontsize=8, color=INK2)
    ax.set_ylabel("runs", fontsize=8, color=INK2)
    ax.set_title(title, fontsize=10, color=INK, loc="left")
    return _png(fig)


def tornado(rows: list[dict[str, Any]], base: float, title: str) -> bytes:
    fig, ax = _fig(6.0, 0.6 + 0.45 * max(len(rows), 1))
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    labels = [r["parameter"] for r in rows][::-1]
    for i, r in enumerate(rows[::-1]):
        lo, hi = r["c_req_low"] - base, r["c_req_high"] - base
        ax.barh(i, lo, color=S3, height=0.5, edgecolor=SURFACE, linewidth=2)
        ax.barh(i, hi, color=S2, height=0.5, edgecolor=SURFACE, linewidth=2)
        ax.text(lo, i, f" {r['low']} ", va="center", ha="right" if lo < 0 else "left", fontsize=7, color=INK2)
        ax.text(hi, i, f" {r['high']} ", va="center", ha="left" if hi >= 0 else "right", fontsize=7, color=INK2)
    ax.axvline(0, color=INK2, linewidth=0.8)
    ax.set_yticks(range(len(labels)), labels)
    ax.set_xlabel(f"change in C_req vs base {base:,.0f} SU", fontsize=8, color=INK2)
    ax.set_title(title, fontsize=10, color=INK, loc="left")
    return _png(fig)


def host_demand(names: list[str], cap: list[float], demand: list[float], title: str) -> bytes:
    n = len(names)
    fig, ax = _fig(max(5.0, min(10.0, 0.35 * n + 2)), 2.8)
    x = list(range(n))
    ax.bar(x, cap, color="#d9e6f6", width=0.7, label="SU capacity", edgecolor=SURFACE, linewidth=2)
    ax.bar(x, demand, color=S1, width=0.7, label="SU demand", edgecolor=SURFACE, linewidth=2)
    ax.set_xticks(x, [s[:12] for s in names], rotation=60, ha="right", fontsize=6)
    ax.set_ylabel("SU", fontsize=8, color=INK2)
    ax.set_title(title, fontsize=10, color=INK, loc="left")
    ax.legend(fontsize=7, frameon=False, labelcolor=INK2)
    return _png(fig)
