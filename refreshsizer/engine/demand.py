"""Cluster CPU demand in SPEC Units (sections 7.2-7.6) plus memory/storage demand assembly."""

from __future__ import annotations

import math
from collections import defaultdict

from refreshsizer.benchmarks.cache import BenchCache
from refreshsizer.config.schema import Policy
from refreshsizer.engine.growth import grow, growth_factor
from refreshsizer.engine.memory import memory_demand
from refreshsizer.engine.modes import MODE_CONFIDENCE
from refreshsizer.engine.normalize import Capacity, host_capacity, power_capacity
from refreshsizer.engine.results import ClusterDemand, HostCapacity
from refreshsizer.engine.storage import resolve_protection, storage_demand
from refreshsizer.model.entities import (
    Cluster,
    Confidence,
    Explanation,
    SizingMode,
    SourceHost,
    SourceWorkload,
)

RISC = {"POWER", "SPARC", "Itanium", "PA-RISC"}
LICENSED = {"oracle_ee", "oracle_se2", "sql_ent", "sql_std"}


def percentile_nearest_rank(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(1, math.ceil(p / 100.0 * len(s)))
    return s[k - 1]


def dominant(workloads: list[SourceWorkload]) -> tuple[str, str]:
    w_cls: dict[str, float] = defaultdict(float)
    w_crit: dict[str, float] = defaultdict(float)
    for w in workloads:
        weight = w.physc_p95 or w.db_cpu_p95_cores or w.vcpu or 1.0
        w_cls[w.workload_class] += weight
        w_crit[w.criticality] += weight
    cls = max(w_cls, key=lambda k: w_cls[k]) if w_cls else "general_virt"
    crit = max(w_crit, key=lambda k: w_crit[k]) if w_crit else "tier2"
    return cls, crit


def k_arch_for(platform_type: str, wl_class: str, policy: Policy) -> tuple[float, str]:
    if platform_type not in RISC:
        return float(policy.k_arch.get("x86", 1.0)), "x86 -> x86"
    key = f"risc_{wl_class}" if f"risc_{wl_class}" in policy.k_arch else "risc_other"
    return float(policy.k_arch.get(key, 1.10)), f"{platform_type} -> x86, class {wl_class} ({key})"


def _host_util(h: SourceHost, policy: Policy) -> tuple[float | None, str, list[str]]:
    flags: list[str] = []
    if h.cpu_util_p95_pct is not None:
        return h.cpu_util_p95_pct, "p95", flags
    if policy.p95_estimate and h.cpu_util_peak_pct is not None and h.cpu_util_avg_pct is not None:
        est = h.cpu_util_avg_pct + policy.p95_estimate_factor * (h.cpu_util_peak_pct - h.cpu_util_avg_pct)
        flags.append(f"ESTIMATE: {h.host_id}: p95 ~= avg + {policy.p95_estimate_factor} x (peak - avg) = {est:.1f} %")
        return est, "p95 estimate", flags
    if h.cpu_util_peak_pct is not None:
        return h.cpu_util_peak_pct, "peak", flags
    if h.cpu_util_avg_pct is not None:
        v = min(100.0, h.cpu_util_avg_pct * policy.rvtools_burst_factor)
        flags.append(f"ESTIMATE: {h.host_id}: only average CPU; avg x {policy.rvtools_burst_factor} used")
        return v, "average x burst", flags
    return None, "", flags


def compute_demand(cluster: Cluster, mode: SizingMode, policy: Policy, cache: BenchCache, *,
                   name: str | None = None, workloads: list[SourceWorkload] | None = None, share: float = 1.0,
                   is_db_cluster: bool = False) -> ClusterDemand:
    wls = list(cluster.workloads if workloads is None else workloads)
    hosts = list(cluster.hosts)
    years = policy.horizon_years
    wl_class, crit = dominant(wls)
    ptype = cluster.platform_type
    expls: list[Explanation] = []
    flags: list[str] = []
    refs: list[str] = []
    conf = MODE_CONFIDENCE[mode]

    # ---- 7.1 source capacity ------------------------------------------------
    caps: dict[str, Capacity] = {}
    cap: Capacity | None
    h: SourceHost | None
    u: float | None
    hcs: list[HostCapacity] = []
    for h in hosts:
        cap = host_capacity(h, policy, cache)
        caps[h.host_id.lower()] = cap
        flags += cap.flags
        refs += cap.refs
        if cap.confidence.rank < conf.rank and cap.method not in ("exact",):
            conf = Confidence.lowest(conf, cap.confidence)
        hcs.append(HostCapacity(host_id=h.host_id, cpu_name=cap.cpu_name, cores=cap.cores, sockets=h.sockets,
                                spc=round(cap.spc, 4), su_cap=round(cap.su_cap, 3), method=cap.method,
                                confidence=cap.confidence, flags=cap.flags))
    su_total = sum(c.su_cap for c in caps.values())
    cores_total = sum(c.cores for c in caps.values())
    spc_src = su_total / cores_total if cores_total else 0.0
    spc_lo = sum(c.spc_min * c.cores for c in caps.values()) / cores_total if cores_total else 0.0
    spc_hi = sum(c.spc_max * c.cores for c in caps.values()) / cores_total if cores_total else 0.0
    threads = sum((h.cores or 0) * (h.threads_per_core or 2) for h in hosts)
    if hosts:
        expls.append(Explanation(
            step_id="src_capacity", title="Source capacity in SPEC Units",
            formula_latex=r"SU_{cap}(h) = SPC(cpu) \times cores(h)",
            formula_text=" + ".join(f"{c.spc:.3f} x {c.cores}" for c in list(caps.values())[:6])
            + (" + ..." if len(caps) > 6 else "") + f" = {su_total:,.1f} SU",
            inputs={"hosts": len(hosts), "cores": cores_total, "SPC_weighted": round(spc_src, 4)},
            output=round(su_total, 3), unit="SU", source_refs=sorted(set(refs))[:20],
            confidence=Confidence.lowest(*[c.confidence for c in caps.values()])))
    ghz_used = 0.0

    # ---- 7.2 demand by mode ---------------------------------------------------
    d0 = 0.0
    paths: dict[str, float] = {}
    sat = policy.saturation_threshold * 100
    lpar = [w for w in wls if w.physc_p95 is not None]
    awr = [w for w in wls if w.db_cpu_p95_cores is not None]
    vm_ghz = [w for w in wls if w.cpu_ghz_p95 is not None or w.cpu_ghz_peak is not None]
    host_sum = [h for h in hosts if _host_util(h, policy)[0] is not None]
    host_by_id = {h.host_id.lower(): h for h in hosts}

    k_arch_parts: list[tuple[float, float]] = []

    if mode == SizingMode.MEASURED:
        by_ts: dict[str, float] = defaultdict(float)
        for p in cluster.timeseries:
            cap = caps.get(p.host_id.lower())
            if cap:
                by_ts[p.ts] += p.cpu_pct / 100.0 * cap.su_cap
        series = list(by_ts.values())
        d = percentile_nearest_rank(series, policy.percentile)
        u = d / su_total if su_total else 0
        txt = f"p{policy.percentile:g} (nearest rank, {len(series)} samples) of sum_h U_t(h) x SU_cap(h) = {d:,.1f} SU"
        if u > policy.saturation_threshold:
            d *= policy.saturation_uplift
            txt += f"; cluster p95 utilization {u:.0%} > {sat:.0f} % -> x {policy.saturation_uplift}"
        d0 = d * share
        paths["measured"] = d0
        ghz_used = sum((h.cores or 0) * (h.nominal_ghz or 0) for h in hosts) * u * share
        expls.append(Explanation(step_id="d0", title="Measured demand (time-aligned cluster p95)",
                                 formula_latex=r"D_0 = P_{95}\left(\sum_h U_t(h) \cdot SU_{cap}(h)\right)",
                                 formula_text=txt + (f" x share {share:.3f}" if share != 1 else ""),
                                 inputs={"samples": len(series), "cluster_util_p95": round(u, 4)},
                                 output=round(d0, 3), unit="SU", confidence=Confidence.HIGH))
    elif mode == SizingMode.SUMMARY and lpar:
        parts = []
        rperf_total = 0.0
        for w in lpar:
            host = host_by_id.get((w.host_id or "").lower())
            cap = power_capacity(w.system_model or (host.model if host else None), w.smt_mode,
                                 host.cores if host else None, policy)
            if cap and cap.rperf_per_core is not None and cap.rperf_factor is not None:
                rpc = cap.rperf_per_core
                f = cap.rperf_factor
                su = (w.physc_p95 or 0) * rpc * f
                rperf_total += (w.physc_p95 or 0) * rpc
                parts.append(f"{w.physc_p95:g} x {rpc:.4f} x {f}")
                refs += cap.refs
            else:
                c2 = caps.get((w.host_id or "").lower())
                spc = c2.spc if c2 else 5.0
                su = (w.physc_p95 or 0) * spc
                parts.append(f"{w.physc_p95:g} x SPC {spc:.3f}")
                flags.append(f"ESTIMATE: LPAR {w.workload_id}: no rPerf system; SPC per core used")
            d0 += su
            ka, _ = k_arch_for(ptype, w.workload_class, policy)
            k_arch_parts.append((su, ka))
            ghz_used += (w.physc_p95 or 0) * (host.nominal_ghz if host and host.nominal_ghz else 3.5)
        paths["lpar"] = d0
        expls.append(Explanation(
            step_id="d0", title="POWER LPAR demand (physc p95 x rPerf/core x F)",
            formula_latex=r"D_0 = \sum_{lpar} physc_{p95} \times \frac{rPerf(smt)}{cores} \times F",
            formula_text=" + ".join(parts) + f" = {d0:,.2f} SU (rPerf demand {rperf_total:,.1f})",
            inputs={"lpars": len(lpar), "rperf_demand": round(rperf_total, 2)}, output=round(d0, 3), unit="SU",
            confidence=Confidence.MEDIUM, assumptions=["rPerf->SPEC conversion factor F; cross-architecture"]))
    elif mode == SizingMode.SUMMARY and awr:
        parts = []
        for w in awr:
            c2 = caps.get((w.host_id or "").lower())
            spc = c2.spc if c2 else 5.0
            su = (w.db_cpu_p95_cores or 0) * spc
            d0 += su
            parts.append(f"{w.db_cpu_p95_cores:g} x {spc:.3f}")
            h = host_by_id.get((w.host_id or "").lower())
            ghz_used += (w.db_cpu_p95_cores or 0) * ((h.nominal_ghz if h else None) or 2.5)
        paths["awr"] = d0
        expls.append(Explanation(step_id="d0", title="Oracle AWR demand (DB CPU p95 x SPC)",
                                 formula_latex=r"D_0 = \sum_{db} DBCPU_{p95} \times SPC",
                                 formula_text=" + ".join(parts) + f" = {d0:,.2f} SU",
                                 inputs={"databases": len(awr)}, output=round(d0, 3), unit="SU",
                                 confidence=Confidence.MEDIUM,
                                 assumptions=["DB portion only: non-database host CPU is not included"]))
    elif mode == SizingMode.SUMMARY and host_sum:
        parts = []
        bases = set()
        for h in hosts:
            cap = caps[h.host_id.lower()]
            u, basis, f2 = _host_util(h, policy)
            flags += f2
            if u is None:
                u = policy.inventory_assumed_util * 100
                basis = "assumed"
                flags.append(f"ASSUMPTION: {h.host_id}: no utilization; assumed {u:.0f} %")
            bases.add(basis)
            su = u / 100.0 * cap.su_cap
            if u > sat:
                su *= policy.saturation_uplift
                flags.append(f"{h.host_id}: utilization {u:.0f} % > {sat:.0f} % -> saturation uplift x "
                             f"{policy.saturation_uplift}")
            d0 += su
            ghz_used += cap.cores * (h.nominal_ghz or 0) * u / 100.0
            parts.append(f"{u:g}% x {cap.su_cap:,.1f}")
            for hc in hcs:
                if hc.host_id == h.host_id:
                    hc.util_used_pct, hc.util_basis, hc.demand_su = u, basis, round(su, 3)
        if "peak" in bases:
            flags.append("SUMMARY mode uses host PEAK CPU (no p95 available): conservative")
        d0 *= share
        ghz_used *= share
        paths["host_summary"] = d0
        expls.append(Explanation(
            step_id="d0", title="Summary demand (sum of host p95/peak)",
            formula_latex=r"D_0 = \sum_h U(h) \times SU_{cap}(h)",
            formula_text=" + ".join(parts[:8]) + (" + ..." if len(parts) > 8 else "")
            + (f" x share {share:.3f}" if share != 1 else "") + f" = {d0:,.2f} SU",
            inputs={"basis": ", ".join(sorted(bases)), "hosts": len(hosts), "share": round(share, 4)},
            output=round(d0, 3), unit="SU", confidence=Confidence.MEDIUM))
    elif mode == SizingMode.SUMMARY and vm_ghz:
        coinc = policy.vm_peak_coincidence
        tot_cores = 0.0
        for w in vm_ghz:
            h = host_by_id.get((w.host_id or "").lower())
            cap = caps.get((w.host_id or "").lower())
            ghz_core = (h.nominal_ghz if h and h.nominal_ghz else 2.4)
            spc = cap.spc if cap else spc_src or 5.0
            if w.cpu_ghz_p95 is not None:
                ghz, c = w.cpu_ghz_p95, 1.0
            else:
                ghz, c = (w.cpu_ghz_peak or 0), coinc
            vm_cores = ghz / ghz_core
            tot_cores += vm_cores
            d0 += vm_cores * spc * c
            ghz_used += ghz * c
        paths["vm_ghz"] = d0
        expls.append(Explanation(
            step_id="d0", title="VM-level demand (GHz -> cores -> SU)",
            formula_latex=r"D_0 = \sum_{vm} \frac{GHz_{p95}}{GHz_{nominal}} \times SPC \times coincidence",
            formula_text=f"{len(vm_ghz)} VMs, {tot_cores:,.1f} equivalent cores, coincidence {coinc} (peaks) "
                         f"-> {d0:,.2f} SU", inputs={"vms": len(vm_ghz), "coincidence": coinc},
            output=round(d0, 3), unit="SU", confidence=Confidence.MEDIUM,
            assumptions=[f"per-VM peaks are not summed directly: x vm_peak_coincidence {coinc}"]))
    elif mode == SizingMode.SNAPSHOT:
        burst = policy.rvtools_burst_factor
        snap_hosts = [h for h in hosts if h.cpu_util_snapshot_pct is not None]
        if snap_hosts:
            parts = []
            for h in hosts:
                cap = caps[h.host_id.lower()]
                u = h.cpu_util_snapshot_pct
                if u is None:
                    u = policy.inventory_assumed_util * 100
                    flags.append(f"ASSUMPTION: {h.host_id}: no snapshot; assumed {u:.0f} %")
                ub = min(100.0, u * burst)
                su = ub / 100 * cap.su_cap
                if ub > sat:
                    su *= policy.saturation_uplift
                d0 += su
                ghz_used += cap.cores * (h.nominal_ghz or 0) * ub / 100
                parts.append(f"{u:g}% x {burst} x {cap.su_cap:,.1f}")
                for hc in hcs:
                    if hc.host_id == h.host_id:
                        hc.util_used_pct, hc.util_basis, hc.demand_su = ub, f"snapshot x {burst}", round(su, 3)
            d0 *= share
            ghz_used *= share
            txt = " + ".join(parts[:8]) + (" + ..." if len(parts) > 8 else "")
        else:
            for w in wls:
                h = host_by_id.get((w.host_id or "").lower())
                cap = caps.get((w.host_id or "").lower())
                ghz_core = h.nominal_ghz if h and h.nominal_ghz else 2.4
                vm_cores = (w.cpu_mhz_snapshot or 0) / 1000 / ghz_core
                d0 += vm_cores * (cap.spc if cap else spc_src or 5) * burst
                ghz_used += (w.cpu_mhz_snapshot or 0) / 1000 * burst
            txt = f"sum of VM snapshot MHz -> cores x SPC x {burst}"
        paths["snapshot"] = d0
        flags.append(f"SNAPSHOT mode: single point-in-time utilization x burst factor {burst}")
        expls.append(Explanation(step_id="d0", title="Snapshot demand (RVTools) x burst factor",
                                 formula_latex=r"D_0 = \sum_h U_{snap}(h) \times k_{burst} \times SU_{cap}(h)",
                                 formula_text=txt + f" = {d0:,.2f} SU", inputs={"burst": burst},
                                 output=round(d0, 3), unit="SU", confidence=Confidence.LOW))
    else:  # INVENTORY_ONLY (or SUMMARY without usable data)
        if mode != SizingMode.INVENTORY_ONLY:
            flags.append("no usable utilization path found; inventory-only method applied")
            mode = SizingMode.INVENTORY_ONLY
            conf = Confidence.VERY_LOW
        a = policy.inventory_assumed_util
        host_path = a * su_total * share
        vcpu = sum(w.vcpu or 0 for w in wls)
        spc_for_vcpu = spc_src or 5.0
        vcpu_path = vcpu * spc_for_vcpu / policy.inventory_vcpu_ratio_assumed
        cands = [v for v in (host_path if hosts else None, vcpu_path if vcpu else None) if v is not None]
        d0 = min(cands) if cands else 0.0
        paths["inventory_host"] = host_path
        paths["inventory_vcpu"] = vcpu_path
        ghz_used = sum((h.cores or 0) * (h.nominal_ghz or 0) for h in hosts) * a * share
        expls.append(Explanation(
            step_id="d0", title="Inventory-only demand (no utilization data)",
            formula_latex=r"D_0 = \min\left(\sum_h u_{assumed} \cdot SU_{cap}(h),\ \sum_{vm} vCPU \cdot SPC / r\right)",
            formula_text=f"min({a:g} x {su_total * share:,.1f}, {vcpu:,.0f} x {spc_for_vcpu:.3f} / "
                         f"{policy.inventory_vcpu_ratio_assumed:g} = {vcpu_path:,.1f}) = {d0:,.1f} SU",
            inputs={"assumed_util": a, "vcpu": vcpu, "vcpu_ratio": policy.inventory_vcpu_ratio_assumed},
            output=round(d0, 3), unit="SU", confidence=Confidence.VERY_LOW,
            assumptions=["configured vCPUs above physical capacity were never real demand (min applies)"]))
        flags.append("INVENTORY_ONLY: VERY LOW confidence - run a 7-30 day Live Optics collection before final BoM")

    # ---- 7.4 adjustments ---------------------------------------------------------
    if k_arch_parts and sum(p[0] for p in k_arch_parts) > 0:
        k_arch = sum(p[0] * p[1] for p in k_arch_parts) / sum(p[0] for p in k_arch_parts)
        k_arch_note = f"{ptype} -> x86, demand-weighted by LPAR class"
    else:
        k_arch, k_arch_note = k_arch_for(ptype, wl_class, policy)
    bare = [h for h in hosts if (h.hypervisor or "").lower() == "bare-metal"]
    k_virt = 1.0
    if policy.target_virtualized and bare and su_total:
        frac = sum(caps[h.host_id.lower()].su_cap for h in bare) / su_total
        k_virt = 1 + (policy.k_virt_baremetal_to_virtual - 1) * frac
    d0_adj = d0 * k_arch * k_virt
    if ptype in RISC:
        flags.append(f"cross-architecture conversion {ptype} -> x86 (k_arch {k_arch:.3f})")
    expls.append(Explanation(
        step_id="adjust", title="Architecture / virtualization / workload adjustments",
        formula_latex=r"D_0' = D_0 \times k_{arch} \times k_{virt} \times k_{wl}",
        formula_text=f"{d0:,.2f} x {k_arch:.4g} x {k_virt:.4g} x 1.0 = {d0_adj:,.2f} SU",
        inputs={"k_arch": round(k_arch, 4), "k_arch_basis": k_arch_note, "k_virt": round(k_virt, 4),
                "k_wl": "per target CPU (1.0 unless workload benchmark exists on both sides)"},
        output=round(d0_adj, 3), unit="SU"))

    # ---- 7.5 growth ----------------------------------------------------------------
    overrides = [(w.growth_override_pct, w.vcpu or w.physc_p95 or 1.0) for w in wls if w.growth_override_pct is not None]
    g = policy.growth.cpu_pct
    if overrides:
        tot_w = sum((w.vcpu or w.physc_p95 or 1.0) for w in wls) or 1.0
        ov_w = sum(o[1] for o in overrides)
        g = (sum(o[0] * o[1] for o in overrides) + g * (tot_w - ov_w)) / tot_w  # type: ignore[operator]
        flags.append(f"workload growth overrides applied: weighted CPU growth {g:.2f} %/yr")
    d_t, gexpl = grow(d0_adj, g, years, policy.growth_events, "su", "growth", "CPU growth over horizon")
    expls.append(gexpl)

    # ---- 7.6 headroom ----------------------------------------------------------------
    u_max = policy.umax(wl_class)
    apply_h = True
    if mode == SizingMode.INVENTORY_ONLY and not policy.inventory_apply_headroom:
        apply_h = False
    c_req = d_t / u_max if apply_h else d_t
    expls.append(Explanation(
        step_id="headroom", title="Headroom", formula_latex=r"C_{req} = D_T / U_{max}(class)",
        formula_text=f"{d_t:,.2f} / {u_max} = {d_t / u_max:,.2f} SU" + ("" if apply_h else " (headroom disabled)"),
        inputs={"D_T": round(d_t, 3), "U_max": u_max, "class": wl_class}, output=round(c_req, 3), unit="SU"))

    # ---- memory / storage -------------------------------------------------------------
    gm = policy.growth.mem_pct
    basis, m0, m_t, mexpl, mflags = memory_demand(wls, hosts, share, policy, mode, years, gm)
    expls.append(mexpl)
    flags += mflags
    external = policy.storage_architecture == "external" or (
        policy.storage_architecture == "auto" and (ptype in RISC or (hosts and len(bare) == len(hosts))))
    prot, prot_note = resolve_protection(policy, bool(external))
    sd = storage_demand(wls, hosts, cluster.storage, share, policy, mode, years, prot)
    expls += sd["explanations"]
    flags += sd["flags"]
    if external:
        flags.append(f"storage: {prot_note}")

    lic: dict[str, dict[str, float]] = {}
    for w in wls:
        if w.licensing and w.licensing != "none":
            e = lic.setdefault(w.licensing, {"workloads": 0, "vcpu": 0.0, "physc": 0.0, "vm_cores_min4": 0.0})
            e["workloads"] += 1
            e["vm_cores_min4"] += max(4.0, w.vcpu or 0)
            e["vcpu"] += w.vcpu or 0
            e["physc"] += w.physc_p95 or 0
    x86_vms = [w for w in wls if w.vcpu and w.physc_p95 is None]
    dem = ClusterDemand(
        cluster=name or cluster.name, source_cluster=cluster.name, mode=mode, confidence=conf, platform_type=ptype,
        wl_class=wl_class, criticality=crit, is_db_cluster=is_db_cluster, external_storage=bool(external),
        years=years, su_cap_total=round(su_total * share, 3), spc_source=round(spc_src, 4),
        spc_source_min=round(spc_lo, 4), spc_source_max=round(spc_hi, 4),
        source_cores=int(round(cores_total * share)), source_threads=int(round(threads * share)),
        source_ghz_used=round(ghz_used, 3), source_hosts=len(hosts), d0=round(d0, 4), d0_by_path=paths,
        k_arch=k_arch, k_virt=k_virt, d0_adj=round(d0_adj, 4), growth_cpu_pct=g, d_t=round(d_t, 4), u_max=u_max,
        c_req=round(c_req, 4), c_req_no_headroom=round(d_t, 4), apply_headroom=apply_h,
        rperf_factor=next((c.rperf_factor for c in caps.values() if c.rperf_factor), None),
        rperf_factor_matched=next((c.rperf_factor_matched for c in caps.values() if c.rperf_factor_matched), None),
        mem_basis=basis, m0=round(m0, 3), growth_mem_pct=gm, m_t=round(m_t, 3),
        storage_used0_tib=round(sd["used0"], 4), storage_used_t_tib=round(sd["used_t"], 4),
        storage_effective_tib=round(sd["effective"], 4), storage_raw_tib=round(sd["raw"], 4), protection=prot,
        p_prot=sd["p_prot"], drr=round(sd["drr"], 4), growth_storage_pct=policy.growth.storage_pct,
        iops0=round(sd["iops0"], 1), iops_t=round(sd["iops_t"], 1), backend_iops_t=round(sd["backend"], 1),
        read_pct=sd["read_pct"], mbps0=round(sd["mbps0"], 1), mbps_t=round(sd["mbps_t"], 1),
        vm_count=len(wls), total_vcpu=sum(w.vcpu or 0 for w in x86_vms),
        largest_vm_vcpu=max((w.vcpu or 0 for w in x86_vms), default=0), licensing=lic, hosts=hcs,
        explanations=expls, flags=list(dict.fromkeys(flags)), source_refs=sorted(set(refs)))
    if mode == SizingMode.INVENTORY_ONLY:
        vcpu_path = paths.get("inventory_vcpu", float("inf")) if wls else float("inf")
        for a in (1.0, 0.7, 0.5):
            hp = a * su_total * share
            dd = min(hp, vcpu_path) if hosts else vcpu_path
            dt = dd * k_arch * k_virt * growth_factor(g, years)
            dem.inventory_sensitivity.append({"assumed_util": a, "d0": round(dd, 2), "d_t": round(dt, 2),
                                              "c_req": round(dt / u_max if apply_h else dt, 2)})
    return dem
