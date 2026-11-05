"""IBM rPerf lookup and rPerf -> SPECrate2017_int_base conversion factor."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from refreshsizer.catalog.loader import load_rperf


@dataclass
class RperfInfo:
    key: str
    model: str
    generation: str
    cores: int
    smt_used: int
    rperf_system: float
    rperf_per_core: float
    url: str | None
    flags: list[str] = field(default_factory=list)


def find_system(model: str | None) -> dict[str, Any] | None:
    if not model:
        return None
    m = re.search(r"\b([SEL])\s*-?\s*(\d{3,4})\b", model, re.I)
    systems = load_rperf().get("systems", [])
    if m:
        key = (m.group(1) + m.group(2)).upper()
        for s in systems:
            if s["key"].upper() == key:
                return dict(s)
    for s in systems:
        if s["model"].lower() in model.lower():
            return dict(s)
    return None


def rperf_per_core(model: str | None, smt: int | None) -> RperfInfo | None:
    s = find_system(model)
    if not s:
        return None
    table = {int(k): float(v) for k, v in s["rperf"].items()}
    want = int(smt or 8)
    flags = []
    if want not in table:
        used = min(table, key=lambda k: abs(k - want))
        flags.append(f"rPerf for SMT{want} not published for {s['model']}; SMT{used} value used")
    else:
        used = want
    total = table[used]
    return RperfInfo(s["key"], s["model"], s["generation"], int(s["cores"]), used, total, total / int(s["cores"]),
                     s.get("url"), flags)


def rperf_factor(generation: str | None, mode: str = "conservative", default: float = 0.425) -> tuple[float, float | None, str]:
    """Return (factor used, generation-matched factor or None, explanation)."""
    data = load_rperf()
    matched = None
    for a in data.get("anchors", []):
        if a.get("factor") and generation and a["generation"].lower() == generation.lower():
            matched = float(a["factor"])
            break
    if mode == "generation_matched" and matched is not None:
        return matched, matched, f"generation-matched anchor for {generation}: F = {matched}"
    note = (f"conservative factor F = {default} (generation-matched value for {generation}: "
            f"{matched if matched is not None else 'no anchor'})")
    return default, matched, note
