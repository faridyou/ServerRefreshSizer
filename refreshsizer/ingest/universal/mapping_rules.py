"""Rules-based column mapping: multilingual synonyms + value heuristics, confidence 0-1 (section 4.6 C1-2)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import yaml
from rapidfuzz import fuzz

from refreshsizer.ingest.common import norm_header, norm_header_nounit
from refreshsizer.ingest.universal.profile import ColumnProfile, TableProfile
from refreshsizer.paths import CONFIG_DIR

CPU_RE = re.compile(r"xeon|epyc|opteron|power\s*\d|sparc|\be5-\d{4}|\b(gold|silver|platinum)\s*\d{4}", re.I)
COMPOSITE_RE = re.compile(r"^\s*\d+\s*[x×*]\s*\S", re.I)
MODEL_RE = re.compile(r"proliant|poweredge|power\s*[se]\d{3}|system\s*x|thinksystem|\bdl\d{3}\b|\b[rm]\d{3}\b|"
                      r"\bsr\d{3}\b|ucs|synergy|\bgen\s?\d+\b", re.I)
OS_RE = re.compile(r"windows|linux|rhel|red hat|centos|ubuntu|suse|sles|aix|solaris|esxi|debian|oracle linux", re.I)
IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
HOSTLIKE_RE = re.compile(r"^[A-Za-z][\w.-]*\d[\w.-]*$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ t]\d{2}:\d{2}|^\d{2}/\d{2}/\d{4} \d{2}:\d{2}")

HOST_FIELDS = ["host_id", "cluster", "site", "vendor", "model", "cpu_model", "cpu_composite", "sockets",
               "cores_per_socket", "total_cores", "threads_per_core", "nominal_ghz", "ram_gb", "mem_used_gb",
               "cpu_util_peak_pct", "cpu_util_p95_pct", "cpu_util_avg_pct", "os", "iops_p95"]
VM_FIELDS = ["vm_name", "host_ref", "cluster", "vcpu", "ram_gb_configured", "storage_used", "storage_provisioned",
             "os", "powerstate", "iops_p95", "workload_class", "licensing", "cpu_util_peak_pct"]
TS_FIELDS = ["timestamp", "host_ref", "cpu_util_avg_pct"]
STORAGE_FIELDS = ["array", "storage_used", "storage_provisioned", "iops_p95"]
ALL_FIELDS = sorted(set(HOST_FIELDS + VM_FIELDS + TS_FIELDS + STORAGE_FIELDS))


@dataclass
class ColumnMapping:
    column: str
    target: str | None
    confidence: float
    method: str
    unit: str | None = None
    extraction: str | None = None


@lru_cache(maxsize=1)
def synonyms() -> dict[str, list[str]]:
    with open(CONFIG_DIR / "synonyms.yaml", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return {k: [str(s) for s in v] for k, v in data.items()}


@lru_cache(maxsize=1)
def _syn_index() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    full: dict[str, list[str]] = {}
    nounit: dict[str, list[str]] = {}
    for field, syns in synonyms().items():
        for s in [field.replace("_", " "), *syns]:
            full.setdefault(norm_header(s), []).append(field)
            nu = norm_header_nounit(s)
            if nu:
                nounit.setdefault(nu, []).append(field)
    return full, nounit


def header_scores(header: str) -> dict[str, float]:
    """Score every canonical field for a header (best of the full header and each ' / ' part)."""
    full, nounit = _syn_index()
    parts = [header] + [p for p in header.split(" / ") if p and p != header]
    scores: dict[str, float] = {}

    def put(field: str, s: float) -> None:
        scores[field] = max(scores.get(field, 0.0), s)

    for i, part in enumerate(parts):
        penalty = 0.0 if i == 0 or i == len(parts) - 1 else 0.05
        n, nu = norm_header(part), norm_header_nounit(part)
        for f in full.get(n, []):
            put(f, 0.95 - penalty)
        for f in nounit.get(nu, []):
            put(f, 0.9 - penalty)
        if nu:
            toks = set(nu.split())
            for key, fields in nounit.items():
                ktoks = set(key.split())
                if len(ktoks) >= 2 and ktoks <= toks:
                    for f in fields:
                        put(f, 0.75 - penalty)
                elif len(key) >= 5:
                    r = fuzz.ratio(nu, key)
                    if r >= 88:
                        for f in fields:
                            put(f, round(r / 100 * 0.8, 3) - penalty)
    return scores


def value_scores(col: ColumnProfile) -> dict[str, float]:
    s = [x for x in col.samples if x]
    if not s:
        return {}
    out: dict[str, float] = {}
    frac = lambda rx: sum(1 for x in s if rx.search(x)) / len(s)  # noqa: E731
    if frac(CPU_RE) >= 0.6:
        out["cpu_composite" if frac(COMPOSITE_RE) >= 0.6 else "cpu_model"] = 0.85
    if frac(MODEL_RE) >= 0.6 and "cpu_model" not in out and "cpu_composite" not in out:
        out["model"] = 0.8
    if frac(OS_RE) >= 0.6:
        out["os"] = 0.8
    if frac(TS_RE) >= 0.8:
        out["timestamp"] = 0.85
    if col.dtype == "text" and frac(HOSTLIKE_RE) >= 0.8 and col.distinct >= max(2, len(s) * 0.8) \
            and not out:
        out["host_id"] = 0.55
        out["vm_name"] = 0.55
    if frac(IP_RE) >= 0.8:
        out["ip"] = 0.9
    return out


def score_columns(t: TableProfile) -> dict[str, dict[str, float]]:
    out = {}
    for c in t.columns:
        sc = header_scores(c.name)
        for f, v in value_scores(c).items():
            # value evidence confirms or adds a target
            sc[f] = max(sc.get(f, 0.0), v if f not in sc else min(0.99, sc[f] + 0.04))
        if "cpu_composite" in value_scores(c) and "cpu_model" in sc:
            sc["cpu_composite"] = max(sc.get("cpu_composite", 0), sc.pop("cpu_model"))
        out[c.name] = sc
    return out


ENTITY_FIELDS = {"physical_hosts": HOST_FIELDS, "vms": VM_FIELDS, "performance_timeseries": TS_FIELDS,
                 "storage_arrays": STORAGE_FIELDS, "datastores": STORAGE_FIELDS}
ALIASES: dict[str, dict[str, str | None]] = {
    "physical_hosts": {"vm_name": None, "host_ref": "host_id", "ram_gb_configured": "ram_gb"},
    "vms": {"host_id": "host_ref", "ram_gb": "ram_gb_configured", "model": None},
    "performance_timeseries": {"host_id": "host_ref", "cpu_util_peak_pct": "cpu_util_avg_pct",
                               "cpu_util_p95_pct": "cpu_util_avg_pct"},
}


def assign(t: TableProfile, classification: str, scores: dict[str, dict[str, float]]) -> list[ColumnMapping]:
    """One-to-one assignment of columns to the fields allowed for the sheet class (greedy by confidence)."""
    allowed = ENTITY_FIELDS.get(classification, [])
    alias = ALIASES.get(classification, {})
    cands: list[tuple[float, str, str]] = []
    for col, sc in scores.items():
        merged: dict[str, float] = {}
        for f, v in sc.items():
            f2 = alias.get(f, f) if f in alias else f
            if f2 and f2 in allowed:
                merged[f2] = max(merged.get(f2, 0), v)
        for f, v in merged.items():
            cands.append((v, col, f))
    # VM sheets: the first host-like column is the VM name, a second one the host reference
    cands.sort(key=lambda x: (-x[0], [c.name for c in t.columns].index(x[1])))
    used_cols: set[str] = set()
    used_fields: set[str] = set()
    res: dict[str, ColumnMapping] = {}
    unit = {c.name: c.unit for c in t.columns}
    for v, col, f in cands:
        if col in used_cols or f in used_fields or v < 0.5:
            continue
        used_cols.add(col)
        used_fields.add(f)
        method = "rules" if v >= 0.7 else "value"
        res[col] = ColumnMapping(col, f, round(v, 3), method, unit.get(col))
    return [res.get(c.name, ColumnMapping(c.name, None, 0.0, "rules", c.unit)) for c in t.columns]
