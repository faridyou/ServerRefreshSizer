"""CPU string normalization and matching to the catalog / benchmark DB (rapidfuzz)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from rapidfuzz import fuzz, process

from refreshsizer.catalog.loader import load_cpus, slugify


@dataclass
class CpuMatch:
    cpu_id: str
    name: str
    confidence: float
    method: str  # exact | pattern | fuzzy
    normalized: str


def normalize_cpu_string(s: str) -> str:
    """'Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz' -> 'Intel Xeon E5-2680 v4';
    'AMD EPYC 9555 64-Core Processor' -> 'AMD EPYC 9555'."""
    t = str(s)
    t = re.sub(r"\((r|tm|c)\)|®|™", " ", t, flags=re.I)
    t = re.sub(r"^\s*\d+\s*[x×*]\s*", " ", t, flags=re.I)  # leading '2x '
    t = re.sub(r"@\s*[\d.,]+\s*ghz", " ", t, flags=re.I)
    t = re.sub(r"\(\s*\d+\s*(c|cores?|coeurs?|cœurs?)\s*\)", " ", t, flags=re.I)
    t = re.sub(r"\b\d+\s*-?\s*core(s)?\b", " ", t, flags=re.I)
    t = re.sub(r"\b(cpu|processor|processeur)\b", " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" ,;-")
    low = t.lower()
    if "xeon" in low and "intel" not in low:
        t = "Intel " + t
    if "epyc" in low and "amd" not in low:
        t = "AMD " + t
    # 'E5-2680v4' -> 'E5-2680 v4'
    t = re.sub(r"(E[357]-\d{4}[A-Z]?)\s*(v\d)", r"\1 \2", t, flags=re.I)
    t = re.sub(r"\bv(\d)\b", r"v\1", t, flags=re.I)
    return t


_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\be([57])-?(\d{4}[a-z]?)\s*(v\d)?\b", re.I), "e5"),
    (re.compile(r"\b(platinum|gold|silver|bronze)\s*(\d{4}[a-z]*\+?)", re.I), "scalable"),
    (re.compile(r"xeon\s*(?:6\s+)?(6\d{3}[pe])\b", re.I), "xeon6"),
    (re.compile(r"epyc\s*(\d{4}[a-z]*)", re.I), "epyc"),
    (re.compile(r"\bpower\s*([89]|10|11)\b", re.I), "power"),
    (re.compile(r"\bsparc\s*(m8|m7|t7|t5|t4|s7)\b", re.I), "sparc"),
]


def pattern_id(s: str) -> str | None:
    for rx, kind in _PATTERNS:
        m = rx.search(s)
        if not m:
            continue
        if kind == "e5":
            ver = (m.group(3) or "").lower()
            return slugify(f"intel xeon e{m.group(1)}-{m.group(2)}" + (f" {ver}" if ver else ""))
        if kind == "scalable":
            return slugify(f"intel xeon {m.group(1)} {m.group(2)}")
        if kind == "xeon6":
            return slugify(f"intel xeon {m.group(1)}")
        if kind == "epyc":
            return slugify(f"amd epyc {m.group(1)}")
        if kind == "power":
            g = m.group(1)
            return "ibm-power10" if g == "10" else f"ibm-power{g}"
        if kind == "sparc":
            g = m.group(1).lower()
            return "oracle-sparc-m8" if g == "m8" else "oracle-sparc-t7"
    low = s.lower()
    if re.search(r"\bpower\s*s8\d\d\b", low) or "power8" in low:
        return "ibm-power8"
    if re.search(r"\bpower\s*[se]9\d\d\b", low):
        return "ibm-power9"
    if re.search(r"\bpower\s*[se]10\d\d\b", low):
        return "ibm-power10"
    return None


@lru_cache(maxsize=1)
def _choices() -> dict[str, str]:
    return {c.model: c.id for c in load_cpus().values()}


def match_cpu(s: str | None, min_score: float = 80.0) -> CpuMatch | None:
    if not s or not str(s).strip():
        return None
    cpus = load_cpus()
    norm = normalize_cpu_string(s)
    pid = pattern_id(norm)
    if pid and pid in cpus:
        return CpuMatch(pid, cpus[pid].model, 1.0, "pattern", norm)
    sid = slugify(norm)
    if sid in cpus:
        return CpuMatch(sid, cpus[sid].model, 1.0, "exact", norm)
    choices = _choices()
    best = process.extractOne(norm, list(choices.keys()), scorer=fuzz.token_sort_ratio)
    if best and best[1] >= min_score:
        # numeric tokens must agree (avoid 6230 ~ 6238)
        nums_in = set(re.findall(r"\d{3,}", norm))
        nums_c = set(re.findall(r"\d{3,}", best[0]))
        if nums_in and nums_c and not (nums_in & nums_c):
            return None
        return CpuMatch(choices[best[0]], best[0], round(best[1] / 100.0, 3), "fuzzy", norm)
    if pid:  # recognised pattern but not in catalog
        return CpuMatch(pid, norm, 0.7, "pattern-uncatalogued", norm)
    return None
