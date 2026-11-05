"""Entity resolution: CPU matching / CPU assumed from server model, fuzzy VM->host joins, de-duplication."""

from __future__ import annotations

import re
from collections import Counter

from rapidfuzz import fuzz, process

from refreshsizer.benchmarks.cpu_matcher import match_cpu
from refreshsizer.benchmarks.spc import resolve_spc
from refreshsizer.catalog.loader import legacy_model_families, load_cpus
from refreshsizer.model.entities import SourceHost, SourceWorkload


def host_key(name: str | None) -> str:
    s = (name or "").strip().lower()
    if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", s):
        s = s.split(".")[0]
    return re.sub(r"[_\s]+", "-", s)


def cpu_from_server_model(model: str) -> tuple[str | None, str]:
    """Most powerful SKU of the most common core count of the newest supported generation (conservative)."""
    fams = legacy_model_families(model)
    if not fams:
        return None, f"server model '{model}' not in legacy catalog"
    cpus = [c for c in load_cpus().values() if c.family == fams[-1]]
    if not cpus:
        return None, f"no CPUs catalogued for {fams[-1]}"
    common = Counter(c.cores for c in cpus).most_common()
    top = max(n for _, n in common)
    core_count = max(c for c, n in common if n == top)
    same = [c for c in cpus if c.cores == core_count]
    best = max(same, key=lambda c: resolve_spc(c.id, chips=2).spc * c.cores)
    return best.model, (f"CPU assumed from server model '{model}': newest supported generation {fams[-1]}, most "
                        f"common core count {core_count}, most powerful SKU {best.model} (conservative)")


def resolve_hosts(hosts: list[SourceHost]) -> tuple[list[SourceHost], list[str]]:
    notes: list[str] = []
    seen: dict[str, SourceHost] = {}
    for h in hosts:
        k = host_key(h.host_id)
        if k in seen:
            notes.append(f"duplicate host '{h.host_id}' merged")
            base = seen[k]
            for f, v in h.model_dump().items():
                if v not in (None, [], "") and getattr(base, f, None) in (None, [], ""):
                    setattr(base, f, v)
            continue
        seen[k] = h
    out = list(seen.values())
    for h in out:
        if h.cpu_model:
            m = match_cpu(h.cpu_model)
            if m:
                if m.method == "fuzzy":
                    notes.append(f"{h.host_id}: CPU '{h.cpu_model}' fuzzy-matched to {m.name} ({m.confidence:.2f})")
                if m.cpu_id in load_cpus() and "POWER" in load_cpus()[m.cpu_id].core_type:
                    h.platform_type = "POWER"
            else:
                notes.append(f"{h.host_id}: CPU '{h.cpu_model}' not recognised")
        elif h.model:
            cpu, why = cpu_from_server_model(h.model)
            if cpu:
                h.cpu_model = cpu
                h.assumptions.append(f"ASSUMPTION: {why}")
                notes.append(f"ASSUMPTION: {h.host_id}: {why}")
                if not h.sockets:
                    h.sockets = 2
                    h.assumptions.append("ASSUMPTION: 2 sockets assumed (typical 2-socket server)")
                if not h.cores_per_socket and not h.total_cores:
                    m2 = match_cpu(cpu)
                    if m2 and m2.cpu_id in load_cpus():
                        h.cores_per_socket = load_cpus()[m2.cpu_id].cores
            else:
                notes.append(f"{h.host_id}: {why}")
        if h.nominal_ghz is None and h.cpu_model:
            m3 = match_cpu(h.cpu_model)
            if m3 and m3.cpu_id in load_cpus() and load_cpus()[m3.cpu_id].base_ghz:
                h.nominal_ghz = load_cpus()[m3.cpu_id].base_ghz
                h.assumptions.append(f"ASSUMPTION: nominal GHz {h.nominal_ghz} taken from CPU catalog")
        if h.total_cores is None and h.sockets and h.cores_per_socket:
            h.total_cores = h.sockets * h.cores_per_socket
        if h.cores_per_socket is None and h.total_cores and h.sockets:
            h.cores_per_socket = h.total_cores // h.sockets
    return out, notes


def join_vms(hosts: list[SourceHost], vms: list[SourceWorkload]) -> list[str]:
    notes: list[str] = []
    idx = {host_key(h.host_id): h for h in hosts}
    keys = list(idx)
    orphans = 0
    renamed: dict[tuple[str, str], int] = {}
    for w in vms:
        if not w.host_id:
            continue
        k = host_key(w.host_id)
        h = idx.get(k)
        if h is None and keys:
            best = process.extractOne(k, keys, scorer=fuzz.ratio)
            if best and best[1] >= 90:
                h = idx[best[0]]
                notes.append(f"VM {w.workload_id}: host '{w.host_id}' fuzzy-joined to '{h.host_id}' ({best[1]:.0f})")
        if h is None:
            orphans += 1
            continue
        if w.host_id != h.host_id:
            renamed[(w.host_id, h.host_id)] = renamed.get((w.host_id, h.host_id), 0) + 1
        w.host_id = h.host_id
        if w.cluster in ("default", None):
            w.cluster = h.cluster
    for (a, b), n in renamed.items():
        notes.append(f"host name '{a}' (VM sheet, {n} VMs) joined to host '{b}' (normalized key)")
    if orphans:
        notes.append(f"{orphans} VM(s) reference unknown hosts (orphans)")
    return notes
