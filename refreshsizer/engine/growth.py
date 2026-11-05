"""Compound growth with optional step events (section 7.5)."""

from __future__ import annotations

from refreshsizer.config.schema import GrowthEvent
from refreshsizer.model.entities import Explanation


def growth_factor(pct: float, years: int) -> float:
    return float((1.0 + pct / 100.0) ** years)


def grow(value: float, pct: float, years: int, events: list[GrowthEvent] | None = None, kind: str = "su",
         step_id: str = "growth", title: str = "Growth", unit: str = "SU") -> tuple[float, Explanation]:
    f = growth_factor(pct, years)
    added = 0.0
    for e in events or []:
        if e.year <= years:
            added += float(getattr(e, kind, 0.0) or 0.0)
    out = value * f + added
    expl = Explanation(
        step_id=step_id, title=title,
        formula_latex=r"D_T = D_0' \times (1+g)^T + \sum \text{events}",
        formula_text=f"{value:,.1f} x (1 + {pct:g} %)^{years} + {added:,.1f} = {out:,.1f}",
        inputs={"D0'": round(value, 3), "g_pct": pct, "T_years": years, "factor": round(f, 6), "events": added},
        output=round(out, 3), unit=unit)
    return out, expl
