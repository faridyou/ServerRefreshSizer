"""Consolidation of several source clusters into one target cluster (policy `consolidation: single`).

Every source cluster keeps its own demand calculation (mode, utilization, k_arch / k_virt, growth, U_max of its
workload class, memory, storage); the target cluster then needs the SUM of those demands:
D0, D0', D_T and C_req are added (so each source keeps its own headroom), memory M_T and storage are added, and the
growth step events of the policy are applied once to the total (not once per source cluster). Ratios used later
(SPC of the source estate, U_max, DRR, growth) are the demand-weighted values implied by those sums.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from refreshsizer.config.schema import Policy
from refreshsizer.engine.results import ClusterDemand, LicenseLine
from refreshsizer.model.entities import Confidence, Explanation, SizingMode
from refreshsizer.units import UNIT

MODE_ORDER = [SizingMode.MEASURED, SizingMode.SUMMARY, SizingMode.SNAPSHOT, SizingMode.INVENTORY_ONLY]
CONF_ORDER = [Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW, Confidence.VERY_LOW]


def _sum(parts: list[ClusterDemand], attr: str) -> float:
    return float(sum(getattr(p, attr) for p in parts))


def merge_demands(parts: list[ClusterDemand], name: str, policy: Policy, events: bool = True) -> ClusterDemand:
    """One target demand from several source-cluster demands computed WITHOUT growth events.

    events: add the policy growth events to this target (only one target cluster of the estate gets them)."""
    if len(parts) == 1 and not (events and policy.growth_events):
        return parts[0].model_copy(update={"cluster": name, "source_clusters": [parts[0].source_cluster]})
    main = max(parts, key=lambda p: p.d_t)
    years = main.years
    sources = list(dict.fromkeys(p.source_cluster for p in parts))
    cores = _sum(parts, "source_cores")
    su_cap = _sum(parts, "su_cap_total")
    d0, d0_adj, d_t, c_req = (_sum(parts, a) for a in ("d0", "d0_adj", "d_t", "c_req"))
    d_t_nh = _sum(parts, "c_req_no_headroom")
    g_cpu = ((d_t / d0_adj) ** (1 / years) - 1) * 100 if d0_adj and d_t else policy.growth.cpu_pct
    u_pre = d_t / c_req if c_req else main.u_max
    m0, m_t = _sum(parts, "m0"), _sum(parts, "m_t")
    used0, used_t = _sum(parts, "storage_used0_tib"), _sum(parts, "storage_used_t_tib")
    eff, raw = _sum(parts, "storage_effective_tib"), _sum(parts, "storage_raw_tib")
    expl_lines = [f"{p.cluster}: D0 {p.d0:,.1f}, D_T {p.d_t:,.1f}, C_req {p.c_req:,.1f} (U_max {p.u_max:g}), "
                  f"M_T {p.m_t:,.0f} GB, storage {p.storage_used_t_tib:,.1f} TiB" for p in parts]
    # policy growth events, once for the whole target cluster
    ev = [e for e in policy.growth_events if e.year <= years] if events else []
    ev_su, ev_mem, ev_tib = (sum(getattr(e, a) for e in ev) for a in ("su", "mem_gib", "storage_tib"))
    if ev_su:
        d_t += ev_su
        d_t_nh += ev_su
        c_req += ev_su / u_pre
    if ev_mem:
        m_t += ev_mem * (1 + policy.hypervisor_mem_overhead) / (1 - policy.mem_free_reserve)
    if ev_tib:
        used_t += ev_tib
        eff += ev_tib / policy.drr_for("default")
        raw += ev_tib / policy.drr_for("default") * main.p_prot / (1 - policy.slack)
    if ev:
        expl_lines.append(f"growth events once for the cluster: +{ev_su:g} {UNIT}, +{ev_mem:g} GB, "
                          f"+{ev_tib:g} TiB")
    iops_t = _sum(parts, "iops_t")
    lic: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for p in parts:
        for k, v in p.licensing.items():
            for f, x in v.items():
                lic[k][f] += x
    paths: dict[str, float] = defaultdict(float)
    for p in parts:
        for k, val in p.d0_by_path.items():
            paths[k] += val
    sens: list[dict[str, Any]] = []
    rows = [p.inventory_sensitivity for p in parts]
    if rows and all(r and [x["assumed_util"] for x in r] == [x["assumed_util"] for x in rows[0]] for r in rows):
        for i, base in enumerate(rows[0]):
            sens.append({"assumed_util": base["assumed_util"],
                         **{k: round(sum(r[i][k] for r in rows), 2) for k in ("d0", "d_t", "c_req")}})
    src_hosts = {p.source_cluster: p.source_hosts for p in parts}
    explanation = Explanation(
        step_id="consolidation", title="Consolidation of source clusters",
        formula_latex=r"D_T = \sum_c D_T(c);\ C_{req} = \sum_c D_T(c) / U_{max}(c);\ M_T = \sum_c M_T(c)",
        formula_text="; ".join(expl_lines) + f" -> total D_T {d_t:,.1f}, C_req {c_req:,.1f}, M_T {m_t:,.0f} GB, "
                                             f"storage {used_t:,.1f} TiB",
        inputs={p.cluster: {"D_T": round(p.d_t, 2), "C_req": round(p.c_req, 2), "M_T": round(p.m_t),
                            "used_T_TiB": round(p.storage_used_t_tib, 2)} for p in parts},
        output=round(c_req, 3), unit=UNIT,
        assumptions=["each source cluster keeps its own mode, adjustments, growth and U_max; the target cluster "
                     "hosts their sum (one HA reserve for the whole cluster)"])
    expls = [explanation] + [e.model_copy(update={"title": f"[{p.cluster}] {e.title}"})
                             for p in parts for e in p.explanations]
    return ClusterDemand(
        cluster=name, source_cluster=" + ".join(sources), source_clusters=sources,
        mode=max((p.mode for p in parts), key=MODE_ORDER.index),
        confidence=max((p.confidence for p in parts), key=CONF_ORDER.index),
        platform_type=main.platform_type, wl_class=main.wl_class, criticality=main.criticality,
        is_db_cluster=all(p.is_db_cluster for p in parts), external_storage=main.external_storage, years=years,
        su_cap_total=round(su_cap, 3), spc_source=round(su_cap / cores, 4) if cores else main.spc_source,
        spc_source_min=round(sum(p.spc_source_min * p.source_cores for p in parts) / cores, 4) if cores else 0,
        spc_source_max=round(sum(p.spc_source_max * p.source_cores for p in parts) / cores, 4) if cores else 0,
        source_cores=int(cores), source_threads=int(_sum(parts, "source_threads")),
        source_ghz_used=round(_sum(parts, "source_ghz_used"), 3),
        source_ghz_installed=round(_sum(parts, "source_ghz_installed"), 3),
        used_cores_equiv=round(_sum(parts, "used_cores_equiv"), 3), source_hosts=int(sum(src_hosts.values())),
        d0=round(d0, 4), d0_by_path={k: round(v, 3) for k, v in paths.items()},
        k_arch=round(d0_adj / d0, 4) if d0 else 1.0, k_virt=1.0, d0_adj=round(d0_adj, 4),
        growth_cpu_pct=round(g_cpu, 4), d_t=round(d_t, 4), u_max=round(d_t / c_req, 4) if c_req else main.u_max,
        c_req=round(c_req, 4), c_req_no_headroom=round(d_t_nh, 4), apply_headroom=any(p.apply_headroom for p in parts),
        rperf_factor=next((p.rperf_factor for p in parts if p.rperf_factor), None),
        rperf_factor_matched=next((p.rperf_factor_matched for p in parts if p.rperf_factor_matched), None),
        mem_basis=" / ".join(dict.fromkeys(p.mem_basis for p in parts)), m0=round(m0, 3),
        growth_mem_pct=main.growth_mem_pct, m_t=round(m_t, 3),
        storage_used0_tib=round(used0, 4), storage_used_t_tib=round(used_t, 4),
        storage_effective_tib=round(eff, 4), storage_raw_tib=round(raw, 4), protection=main.protection,
        p_prot=main.p_prot, drr=round(used_t / eff, 4) if eff else main.drr, growth_storage_pct=main.growth_storage_pct,
        iops0=round(_sum(parts, "iops0"), 1), iops_t=round(iops_t, 1),
        backend_iops_t=round(_sum(parts, "backend_iops_t"), 1),
        read_pct=round(sum(p.read_pct * p.iops_t for p in parts) / iops_t, 2) if iops_t else main.read_pct,
        mbps0=round(_sum(parts, "mbps0"), 1), mbps_t=round(_sum(parts, "mbps_t"), 1),
        vm_count=int(_sum(parts, "vm_count")), inactive_vms=int(_sum(parts, "inactive_vms")),
        inactive_storage_tib=round(_sum(parts, "inactive_storage_tib"), 4), total_vcpu=_sum(parts, "total_vcpu"),
        largest_vm_vcpu=max(p.largest_vm_vcpu for p in parts), licensing={k: dict(v) for k, v in lic.items()},
        hosts=[h for p in parts for h in p.hosts], explanations=expls,
        flags=list(dict.fromkeys([f"consolidated from {len(sources)} source clusters: {', '.join(sources)}"]
                                 + [f"[{p.cluster}] {f}" for p in parts for f in p.flags])),
        assumptions=list(dict.fromkeys(a for p in parts for a in p.assumptions)),
        source_refs=sorted({r for p in parts for r in p.source_refs}), inventory_sensitivity=sens)


def merge_license_lines(groups: list[list[LicenseLine]]) -> list[LicenseLine]:
    """Source-estate licensing of several clusters: same product and metric -> quantities and costs added."""
    out: dict[tuple[str, str], LicenseLine] = {}
    for lines in groups:
        for ln in lines:
            key = (ln.product, ln.metric)
            if key not in out:
                out[key] = ln.model_copy()
                continue
            o = out[key]
            o.quantity += ln.quantity
            o.one_time_usd += ln.one_time_usd
            o.annual_usd += ln.annual_usd
            o.horizon_usd += ln.horizon_usd
    return list(out.values())
