"""Explanation helpers for templates."""

from __future__ import annotations

from typing import Any


def fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        if abs(v) >= 1000:
            return f"{v:,.1f}"
        return f"{v:.4g}" if abs(v) < 1 else f"{v:,.3f}".rstrip("0").rstrip(".")
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, dict):
        return ", ".join(f"{k}: {fmt(x)}" for k, x in v.items())
    if isinstance(v, list):
        return ", ".join(fmt(x) for x in v)
    return "" if v is None else str(v)


def money(v: Any) -> str:
    try:
        f = float(v)
        return f"-${-f:,.0f}" if f < 0 else f"${f:,.0f}"
    except (TypeError, ValueError):
        return ""


def pct(v: Any) -> str:
    try:
        return f"{float(v):.1%}"
    except (TypeError, ValueError):
        return ""
