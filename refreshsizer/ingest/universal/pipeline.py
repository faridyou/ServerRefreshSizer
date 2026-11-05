"""Universal ingest: profile -> classify -> map (rules + optional LLM) -> review -> normalize -> resolve."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from refreshsizer.config.schema import Policy
from refreshsizer.ingest.base import ParseResult
from refreshsizer.ingest.common import to_bool, to_float, to_int, to_str
from refreshsizer.ingest.universal import profiles_store
from refreshsizer.ingest.universal.classify import classify
from refreshsizer.ingest.universal.mapping_llm import llm_enabled, run_llm
from refreshsizer.ingest.universal.mapping_rules import ENTITY_FIELDS, ColumnMapping, assign, score_columns
from refreshsizer.ingest.universal.normalize import convert, is_fraction_column, parse_composite_cpu
from refreshsizer.ingest.universal.profile import TableProfile, profile_file
from refreshsizer.ingest.universal.resolve import host_key, join_vms, resolve_hosts
from refreshsizer.ingest.universal.review import ReviewIO, review
from refreshsizer.model.entities import (
    LICENSING,
    WORKLOAD_CLASSES,
    Assumption,
    MappingEntry,
    SheetUse,
    SourceHost,
    SourceWorkload,
    StorageArray,
    TimeSeriesPoint,
)

log = logging.getLogger("refreshsizer.universal")
TEXT_FIELDS = {"host_id", "vm_name", "host_ref", "cluster", "site", "vendor", "model", "cpu_model", "os", "array",
               "workload_class", "licensing", "timestamp"}
INT_FIELDS = {"sockets", "cores_per_socket", "total_cores"}
ID_FIELDS = {"host_id", "vm_name", "host_ref", "cluster", "site", "array"}
TOTAL_KEYS = {
    "host_count": ["nombre de serveurs", "number of servers", "servers", "hosts", "nombre d hotes", "nb serveurs"],
    "vm_count": ["nombre de vm", "number of vms", "vms", "nb vm", "vm count"],
    "ram_gb": ["memoire totale", "total memory", "ram totale", "memory"],
    "total_cores": ["coeurs totaux", "total cores", "cores", "nombre de coeurs"],
    "storage_used_tib": ["stockage utilise", "storage used", "used storage"],
}


def _value(field: str, raw: Any, unit: str | None, frac: bool) -> Any:
    if raw is None:
        return None
    if field in TEXT_FIELDS:
        return to_str(raw)
    if field in INT_FIELDS:
        return to_int(raw)
    if field == "threads_per_core":
        if isinstance(raw, bool) or str(raw).lower() in ("true", "false", "yes", "no", "oui", "non"):
            return 2 if to_bool(raw) else 1
        return to_int(raw)
    if field == "powerstate":
        return to_bool(raw, True)
    return convert(field, raw, unit, frac)


def _row_values(t: TableProfile, row: dict[str, Any], maps: list[ColumnMapping], fracs: set[str]) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for m in maps:
        if not m.target:
            continue
        raw = row.get(m.column)
        if m.target == "cpu_composite":
            p = parse_composite_cpu(raw)
            if p:
                for k, v in p.items():
                    d.setdefault(k, v)
            elif raw is not None:
                d.setdefault("cpu_model", to_str(raw))
            continue
        v = _value(m.target, raw, m.unit, m.column in fracs)
        if v is not None:
            d[m.target] = v
    return d


def _samples(t: TableProfile, maps: list[ColumnMapping], fracs: set[str]) -> dict[str, tuple[str, str]]:
    out = {}
    first = t.rows[0] if t.rows else {}
    for m in maps:
        raw = first.get(m.column)
        tr = ""
        if m.target:
            if m.target == "cpu_composite":
                tr = str(parse_composite_cpu(raw))
            else:
                tr = str(_value(m.target, raw, m.unit, m.column in fracs))
        out[m.column] = ("" if raw is None else str(raw), tr)
    return out


def _totals(t: TableProfile) -> dict[str, float]:
    from refreshsizer.ingest.common import norm_header_nounit

    out: dict[str, float] = {}
    row = t.rows[0] if t.rows else {}
    for k, v in row.items():
        nk = norm_header_nounit(k)
        for key, syns in TOTAL_KEYS.items():
            if any(nk == s or nk.startswith(s) for s in syns):
                f = to_float(v)
                if f is not None:
                    out.setdefault(key, f)
    return out


def universal_ingest(files: list[Path], *, policy: Policy, review_mode: str = "yes", llm: str | bool = "auto",
                     llm_client: Any = None, io: ReviewIO | None = None,
                     profiles_dir: Path | None = None) -> list[ParseResult]:
    names = [Path(f).name for f in files]
    res = ParseResult(fmt="universal", file=", ".join(names), utilization_source="universal")
    tables: list[TableProfile] = []
    for f in files:
        try:
            wp = profile_file(Path(f))
        except Exception as e:  # unreadable file: report, never crash
            res.errors.append(f"{Path(f).name}: cannot read ({type(e).__name__}: {e})")
            continue
        tables += wp.tables
        res.transformations += [f"{wp.file}: {n}" for n in wp.notes]
        with_tables = {t.sheet for t in wp.tables}
        for s in wp.sheets:
            if s not in with_tables:
                res.sheets.append(SheetUse(file=wp.file, sheet=s, used=False, classification="irrelevant",
                                           reason="no table found (title/notes only)"))

    plan: dict[str, dict[str, Any]] = {}
    for t in tables:
        prof = profiles_store.load(t, profiles_dir)
        if prof:
            maps = [ColumnMapping(m.column, m.target, 1.0, "profile", m.unit) for m in prof.mappings]
            known = {m.column for m in maps}
            maps += [ColumnMapping(c.name, None, 0.0, "profile", c.unit) for c in t.columns if c.name not in known]
            plan[t.table_id] = {"cls": prof.classification, "conf": 1.0, "reason": "saved mapping profile",
                                "maps": maps, "profile": True}
            continue
        scores = score_columns(t)
        cls, conf, reason = classify(t, scores)
        maps = assign(t, cls, scores) if cls in ENTITY_FIELDS else [
            ColumnMapping(c.name, None, 0.0, "rules", c.unit) for c in t.columns]
        plan[t.table_id] = {"cls": cls, "conf": conf, "reason": reason, "maps": maps, "profile": False}

    # ---- optional LLM step for unresolved / low-confidence tables ---------------------
    use_llm = llm not in (False, "false", "off") and (llm_client is not None or llm_enabled(policy.llm_enabled))
    res_status = "disabled (rules-only)"
    if use_llm:
        pending = [t for t in tables if not plan[t.table_id]["profile"] and (
            plan[t.table_id]["conf"] < policy.mapping_autoaccept
            or any(m.target is None and t.columns[i].dtype != "empty" for i, m in enumerate(plan[t.table_id]["maps"]))
            or any(m.target and m.confidence < policy.mapping_autoaccept for m in plan[t.table_id]["maps"]))]
        if pending:
            id_cols = {t.table_id: {m.column for m in plan[t.table_id]["maps"] if m.target in ID_FIELDS}
                       for t in pending}
            resp, res_status, _ = run_llm(pending, policy.llm_model, id_cols, client=llm_client,
                                          customer_names=getattr(policy, "customer_names", None))
            if resp:
                for lt in resp.tables:
                    p = plan.get(lt.table_id)
                    if not p:
                        continue
                    if p["conf"] < 0.6 or p["cls"] == "irrelevant":
                        p["cls"], p["reason"] = lt.classification, "LLM classification"
                    allowed = ENTITY_FIELDS.get(p["cls"], [])
                    by_col = {m.column: m for m in p["maps"]}
                    taken = {m.target for m in p["maps"] if m.target and m.confidence >= 0.9}
                    for lc in lt.columns:
                        cur = by_col.get(lc.column)
                        if cur is None or not lc.target or lc.target not in allowed or lc.target in taken:
                            continue
                        if cur.target is None or cur.confidence < lc.confidence:
                            by_col[lc.column] = ColumnMapping(lc.column, lc.target, round(lc.confidence, 3), "llm",
                                                              lc.unit or cur.unit, lc.extraction)
                            taken.add(lc.target)
                    p["maps"] = [by_col[m.column] for m in p["maps"]]
        else:
            res_status = "not needed (rules resolved every table)"
    res.transformations.append(f"LLM: {res_status}")

    # ---- review, extraction -------------------------------------------------------------
    hosts: list[SourceHost] = []
    vms: list[SourceWorkload] = []
    totals: list[tuple[str, dict[str, float]]] = []
    for t in tables:
        p = plan[t.table_id]
        fracs = {m.column for m in p["maps"] if m.target and m.target.endswith("_pct")
                 and is_fraction_column([r.get(m.column) for r in t.rows])}
        label = f"{t.file} / {t.sheet}" + (f" #{t.table_index + 1}" if t.table_index else "")
        maps, notes = review(label, p["maps"], _samples(t, p["maps"], fracs), review_mode, policy, io)
        res.assumptions += [Assumption(text=n) for n in notes]
        used = p["cls"] not in ("irrelevant",)
        res.sheets.append(SheetUse(file=t.file, sheet=t.sheet, used=used, classification=p["cls"],
                                   reason=p["reason"] + (f"; dropped: {', '.join(t.dropped)}" if t.dropped else ""),
                                   rows=len(t.rows)))
        samples = _samples(t, maps, fracs)
        for m in maps:
            res.mappings.append(MappingEntry(file=t.file, sheet=t.sheet, column=m.column, target=m.target,
                                             unit=m.unit, confidence=m.confidence, method=m.method,
                                             sample=samples[m.column][0], transformed=samples[m.column][1],
                                             accepted=m.target is not None))
            if m.target is None and used:
                res.unmapped.append(f"{t.sheet}/{m.column}")
        if fracs:
            res.transformations.append(f"{t.sheet}: 0-1 fractions converted to % in {', '.join(sorted(fracs))}")
        for u in {m.unit for m in maps if m.target and m.unit and m.unit not in ("GiB", "pct")}:
            res.transformations.append(f"{t.sheet}: values in {u} converted to canonical units")
        if t.dropped:
            res.transformations.append(f"{t.sheet}: dropped {len(t.dropped)} row(s): {'; '.join(t.dropped)}")
        if any(m.target == "cpu_composite" for m in maps):
            res.transformations.append(f"{t.sheet}: composite CPU strings split into sockets/CPU model/cores")
        if used and not p["profile"] and any(m.target for m in maps) and p["cls"] in ENTITY_FIELDS:
            profiles_store.save(profiles_store.MappingProfile(
                fingerprint=profiles_store.fingerprint(t), classification=p["cls"],
                headers=[c.name for c in t.columns], source=label,
                mappings=[profiles_store.SavedMapping(column=m.column, target=m.target, unit=m.unit) for m in maps]),
                profiles_dir)
        cls = p["cls"]
        if cls == "physical_hosts":
            for row in t.rows:
                d = _row_values(t, row, maps, fracs)
                hid = d.get("host_id")
                if not hid:
                    continue
                hosts.append(SourceHost(
                    host_id=str(hid), cluster=d.get("cluster") or "default", site=d.get("site"),
                    vendor=d.get("vendor"), model=d.get("model"), cpu_model=d.get("cpu_model"),
                    sockets=d.get("sockets"), cores_per_socket=d.get("cores_per_socket"),
                    total_cores=d.get("total_cores"), threads_per_core=d.get("threads_per_core"),
                    nominal_ghz=d.get("nominal_ghz"), ram_gb=d.get("ram_gb"), mem_used_gb=d.get("mem_used_gb"),
                    cpu_util_p95_pct=d.get("cpu_util_p95_pct"), cpu_util_peak_pct=d.get("cpu_util_peak_pct"),
                    cpu_util_avg_pct=d.get("cpu_util_avg_pct"), iops_p95=d.get("iops_p95"),
                    hypervisor="vSphere" if not d.get("os") else ("bare-metal" if "esx" not in
                                                                  str(d.get("os")).lower() else "vSphere")))
        elif cls == "vms":
            for row in t.rows:
                d = _row_values(t, row, maps, fracs)
                name = d.get("vm_name")
                if not name:
                    continue
                if d.get("powerstate") is False and policy.exclude_powered_off:
                    continue
                wc = str(d.get("workload_class") or "general_virt").lower()
                lic = str(d.get("licensing") or "none").lower()
                vms.append(SourceWorkload(
                    workload_id=str(name), name=str(name), host_id=d.get("host_ref"),
                    cluster=d.get("cluster") or "default", vcpu=d.get("vcpu"),
                    ram_gb_configured=d.get("ram_gb_configured"), storage_used_gib=d.get("storage_used"),
                    storage_provisioned_gib=d.get("storage_provisioned"), os=d.get("os"),
                    iops_p95=d.get("iops_p95"), workload_class=wc if wc in WORKLOAD_CLASSES else "general_virt",
                    licensing=lic if lic in LICENSING else "none"))
        elif cls == "performance_timeseries":
            for row in t.rows:
                d = _row_values(t, row, maps, fracs)
                if d.get("timestamp") and d.get("host_ref") and d.get("cpu_util_avg_pct") is not None:
                    res.timeseries.append(TimeSeriesPoint(ts=str(d["timestamp"]), host_id=str(d["host_ref"]),
                                                          cpu_pct=float(d["cpu_util_avg_pct"])))
        elif cls in ("storage_arrays", "datastores"):
            for row in t.rows:
                d = _row_values(t, row, maps, fracs)
                if d.get("array"):
                    res.storage.append(StorageArray(
                        name=str(d["array"]), used_tib=(d.get("storage_used") or 0) / 1024 or None,
                        raw_tib=(d.get("storage_provisioned") or 0) / 1024 or None, iops_p95=d.get("iops_p95")))
        elif cls == "cluster_totals":
            totals.append((label, _totals(t)))

    hosts, notes = resolve_hosts(hosts)
    res.transformations += notes
    res.assumptions += [Assumption(text=n) for n in notes if n.startswith("ASSUMPTION")]
    res.transformations += join_vms(hosts, vms)
    res.hosts, res.workloads = hosts, vms
    if res.timeseries:
        idx = {host_key(h.host_id): h.host_id for h in hosts}
        for pt in res.timeseries:
            pt.host_id = idx.get(host_key(pt.host_id), pt.host_id)
    # reconciliation of file totals vs recomputed totals (> 2 % gap flagged)
    recomputed = {"host_count": float(len(hosts)), "vm_count": float(len(vms)),
                  "ram_gb": sum(h.ram_gb or 0 for h in hosts), "total_cores": float(sum(h.cores or 0 for h in hosts)),
                  "storage_used_tib": sum(w.storage_used_gib or 0 for w in vms) / 1024}
    for label, tot in totals:
        for k, v in tot.items():
            r = recomputed.get(k)
            if r is None or not v:
                continue
            gap = abs(r - v) / abs(v)
            msg = f"reconciliation {label}: {k} file={v:g} recomputed={r:,.2f} gap={gap:.1%}"
            if gap > 0.02:
                res.warnings.append(msg + " (> 2 %)")
                res.conflicts.append(msg + " -> recomputed value used")
            else:
                res.transformations.append(msg + " OK")
    for t in tables:
        for tot in t.totals:
            res.transformations.append(f"{t.sheet}: subtotal/total row ignored ({', '.join(f'{k}={v}' for k, v in list(tot.items())[:3])})")
    if not hosts and vms:
        res.warnings.append("no physical host table found: host capacity unknown (VM-level data only)")
    return [res]
