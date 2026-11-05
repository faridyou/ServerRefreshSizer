"""Unit conversion and composite-string extraction to canonical units (GiB, GHz, %)."""

from __future__ import annotations

import re
from typing import Any

from refreshsizer.benchmarks.cpu_matcher import normalize_cpu_string
from refreshsizer.ingest.common import to_float

TO_GIB = {"KiB": 1 / (1024 * 1024), "MiB": 1 / 1024, "GiB": 1.0, "TiB": 1024.0}
MEMORY_FIELDS = {"ram_gb", "mem_used_gb", "ram_gb_configured"}
STORAGE_FIELDS = {"storage_used", "storage_provisioned"}
PCT_FIELDS = {"cpu_util_peak_pct", "cpu_util_p95_pct", "cpu_util_avg_pct"}

COMPOSITE_RE = re.compile(
    r"^\s*(?P<n>\d+)\s*[x×*]\s*(?P<cpu>.*?)\s*(?:\(\s*(?P<c>\d+)\s*(?:c|cores?|coeurs?|cœurs?)\s*\))?\s*$", re.I)
SOCKETS_CORES_RE = re.compile(r"^\s*(?P<n>\d+)\s*[x×*]\s*(?P<c>\d+)\s*(?:c|cores?|coeurs?|cœurs?)\s*$", re.I)
RANGE_RE = re.compile(r"^\s*([\d.,]+)\s*-\s*([\d.,]+)\s*(ghz|mhz)?", re.I)


def unit_from_value(v: Any) -> str | None:
    s = str(v).lower()
    for rx, unit in ((r"\b(tib|to|tb)\b", "TiB"), (r"\b(gib|go|gb)\b", "GiB"), (r"\b(mib|mo|mb)\b", "MiB"),
                     (r"\bghz\b", "GHz"), (r"\bmhz\b", "MHz")):
        if re.search(rx, s):
            return unit
    return None


def convert(field: str, value: Any, unit: str | None, fraction_column: bool = False) -> float | None:
    """Convert one raw value to the canonical unit of `field`."""
    if value is None:
        return None
    if field == "nominal_ghz":
        m = RANGE_RE.match(str(value))
        f = to_float(m.group(1)) if m else to_float(value)
        if f is None:
            return None
        u = unit_from_value(value) or unit
        if u == "MHz" or f > 100:
            f = f / 1000.0
        return f
    f = to_float(value)
    if f is None:
        return None
    if field in PCT_FIELDS:
        if "%" in str(value):
            return f
        if fraction_column or (0 < f <= 1.0 and unit != "pct"):
            return round(f * 100.0, 9)
        return f
    if field in MEMORY_FIELDS or field in STORAGE_FIELDS:
        u = unit_from_value(value) or unit or "GiB"
        return f * TO_GIB.get(u, 1.0)
    return f


def parse_composite_cpu(s: Any) -> dict[str, Any] | None:
    """'2x Xeon Gold 6230 (20c)' -> {sockets: 2, cpu_model: 'Intel Xeon Gold 6230', cores_per_socket: 20};
    '2 x 14 cores' -> {sockets: 2, cores_per_socket: 14}."""
    if s is None:
        return None
    txt = str(s)
    m = SOCKETS_CORES_RE.match(txt)
    if m:
        return {"sockets": int(m.group("n")), "cores_per_socket": int(m.group("c"))}
    m = COMPOSITE_RE.match(txt)
    if not m or not m.group("cpu"):
        return None
    out: dict[str, Any] = {"sockets": int(m.group("n")), "cpu_model": normalize_cpu_string(m.group("cpu"))}
    if m.group("c"):
        out["cores_per_socket"] = int(m.group("c"))
    return out


def is_fraction_column(values: list[Any]) -> bool:
    raw = [to_float(v) for v in values if v is not None and "%" not in str(v)]
    nums: list[float] = [n for n in raw if n is not None]
    return bool(nums) and all(0 <= n <= 1.0 for n in nums) and any(0 < n < 1 for n in nums)
