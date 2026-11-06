"""Exhaustive enumeration of target configs with pruning (section 7.13)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from refreshsizer.benchmarks.cache import BenchCache
from refreshsizer.benchmarks.sap_saps import k_wl
from refreshsizer.benchmarks.spc import resolve_spc
from refreshsizer.catalog.dimm_rules import choose_dimms
from refreshsizer.catalog.loader import load_cpus, load_platforms
from refreshsizer.config.schema import Policy
from refreshsizer.engine import ha
from refreshsizer.engine.constraints import numa_check, ratio_max, vcpu_ratio
from refreshsizer.engine.growth import growth_factor
from refreshsizer.engine.licensing import target_licensing
from refreshsizer.engine.network import choose_nics
from refreshsizer.engine.results import Candidate, ClusterDemand
from refreshsizer.engine.storage import choose_drives
from refreshsizer.model.entities import CpuModel, Explanation, TargetConfig, TargetPlatform

WORKLOAD_SUITE = {"saps": "saps", "specjbb2015_maxjops": "specjbb2015"}


def _capex_host(platform: TargetPlatform, cpu: CpuModel, count: int, dimm_gb: int, dimm_size: int,
                drives_tb: float, drive_size: float, nic: str, prices: dict[str, Any]) -> float:
    base = float((prices.get("platform_base_usd") or {}).get(platform.model, 10000))
    cpu_p = float(cpu.list_price_usd or prices.get("cpu_price_default_usd", 3000))
    per_gb = float((prices.get("dimm_usd_per_gb") or {}).get("ddr5", 6.0))
    prem = float((prices.get("dimm_premium_large") or {}).get(dimm_size, 1.0))
    drive = float((prices.get("drive_usd_per_tb") or {}).get(drive_size, 120.0)) * drives_tb if drives_tb else 0
    nic_p = float((prices.get("nic_usd") or {}).get(nic, 1000))
    return base + cpu_p * count + dimm_gb * per_gb * prem + drive + nic_p


def evaluate(d: ClusterDemand, platform: TargetPlatform, cpu: CpuModel, count: int, policy: Policy,
             prices: dict[str, Any], cache: BenchCache) -> Candidate:
    spc = resolve_spc(cpu.id, chips=count, system_hint=platform.model, cache=cache)
    cores = cpu.cores
    su_host = spc.spc * cores * count
    reasons: list[str] = []
    warns: list[str] = list(spc.flags)
    expls: list[Explanation] = []
    # workload-specific k_wl
    metric = policy.class_metric_map.get(d.wl_class, "cpu2017_int_base") if policy.benchmark_metric == "by_class" else ""
    kwl, kwl_note = 1.0, "k_wl = 1.0"
    if metric in WORKLOAD_SUITE and d.hosts:
        from refreshsizer.benchmarks.cpu_matcher import match_cpu

        src = match_cpu(d.hosts[0].cpu_name)
        kwl, kwl_note = k_wl(cache, WORKLOAD_SUITE[metric], src.cpu_id if src else None, cpu.id,
                             d.spc_source, spc.spc)
    c_req = d.c_req * kwl
    d_t = d.d_t * kwl
    vsan = not d.external_storage
    su_eff = su_host
    expls.append(Explanation(
        step_id="target_su", title="Target host capacity",
        formula_latex=r"SU_{host} = SPC_{target} \times cores \times CPUs",
        formula_text=f"{spc.spc:.4f} x {cores} x {count} = {su_host:,.1f} SU ({kwl_note})",
        inputs={"cpu": cpu.model, "SPC": round(spc.spc, 4), "cores_per_cpu": cores, "cpus": count},
        output=round(su_host, 2), unit="SU", source_refs=spc.refs(), confidence=spc.confidence))
    u_check = d.u_max if d.apply_headroom else 1.0
    plan = ha.plan(d_t, c_req, su_eff, policy, d.protection, u_check=u_check)
    if vsan and plan.n_active and d.backend_iops_t / plan.n_total > policy.vsan_high_io_threshold_iops_per_host:
        su_eff = su_host * (1 - policy.vsan_cpu_reserve_high_io)
        warns.append(f"high IO: {policy.vsan_cpu_reserve_high_io:.0%} of host SU reserved for vSAN ESA")
        plan = ha.plan(d_t, c_req, su_eff, policy, d.protection, u_check=u_check)
    n_floor = plan.n_total
    dimm = None
    drives = None
    rmax = ratio_max(d.criticality, d.wl_class, policy)
    vcpu_t = d.total_vcpu * growth_factor(d.growth_cpu_pct, d.years)
    raw = norm = 0.0
    for _ in range(80):
        plan = ha.plan(d_t, c_req, su_eff, policy, d.protection, n_floor=n_floor, u_check=u_check)
        n, k = plan.n_total, plan.k_ha
        if n > policy.max_cluster_size:
            break
        survivors = max(n - k - (1 if policy.maintenance_reserve else 0), 1)
        need_mem = d.m_t / survivors
        dimm = choose_dimms(platform, count, cores, cpu.core_type, cpu.max_mt_s, need_mem)
        if dimm is None:
            n_floor = n + 1
            continue
        if vsan and d.storage_raw_tib > 0:
            drives = choose_drives(d.storage_raw_tib / n, platform.drive_bays, prices, policy.min_drives_per_host)
            if drives is None:
                n_floor = n + 1
                continue
        raw, norm, status = vcpu_ratio(vcpu_t, n * cores * count, spc.spc, d.spc_source or spc.spc, rmax)
        if status == "fail":
            n_floor = n + 1
            continue
        break
    n, k = plan.n_total, plan.k_ha
    if n > policy.max_cluster_size:
        reasons.append(f"needs {n} hosts > max cluster size {policy.max_cluster_size}")
    if dimm is None:
        reasons.append(f"memory {d.m_t:,.0f} GB cannot be met within {policy.max_cluster_size} hosts")
    if vsan and d.storage_raw_tib > 0 and drives is None:
        reasons.append("storage does not fit the drive bays")
    expls.append(plan.explanation)
    survivors = max(n - k, 1)
    if dimm:
        expls.append(Explanation(
            step_id="dimm", title="Memory per host and DIMM population",
            formula_text=(f"{d.m_t:,.0f} GB / ({n} - {k}) = {d.m_t / survivors:,.0f} GB per host -> "
                          f"{dimm.dimm_count} x {dimm.dimm_size_gb} GB = {dimm.total_gb} GB, {dimm.dpc} DPC @ "
                          f"{dimm.speed_mt_s} MT/s, {dimm.gbs_per_core} GB/s per core"),
            inputs={"M_T": round(d.m_t), "hosts_after_failure": survivors}, output=dimm.total_gb, unit="GB"))
        if dimm.gbs_per_core is not None and dimm.gbs_per_core < policy.mem_bw_min_gbs_per_core and d.wl_class in (
                "hpc", "analytics", "dwh"):
            warns.append(f"memory bandwidth {dimm.gbs_per_core} GB/s/core < {policy.mem_bw_min_gbs_per_core} for "
                         f"{d.wl_class}")
        if dimm.dpc > 1:
            warns.append(f"2 DPC required: memory speed derated to {dimm.speed_mt_s} MT/s")
    if drives:
        expls.append(Explanation(
            step_id="drives", title="Drives per host",
            formula_text=(f"{d.storage_raw_tib:,.1f} TiB raw / {n} hosts = {d.storage_raw_tib / n:,.2f} TiB -> "
                          f"{drives.count} x {drives.drive_tb} TB NVMe = {drives.raw_tib} TiB (cheapest $/TB that "
                          f"fits {platform.drive_bays} bays, min {policy.min_drives_per_host})"),
            inputs={"raw_TiB": round(d.storage_raw_tib, 2), "hosts": n}, output=drives.count, unit="drives"))
    numa_ok = True
    if d.platform_type == "x86" and d.largest_vm_vcpu:
        numa_ok, nexpl = numa_check(d.largest_vm_vcpu, cores, policy)
        expls.append(nexpl)
        if not numa_ok:
            reasons.append(f"largest VM {d.largest_vm_vcpu:g} vCPU exceeds cores per NUMA node {cores}")
    if d.total_vcpu:
        raw, norm, status = vcpu_ratio(vcpu_t, n * cores * count, spc.spc, d.spc_source or spc.spc, rmax)
        expls.append(Explanation(
            step_id="vcpu_ratio", title="vCPU:pCore ratio",
            formula_text=(f"raw {vcpu_t:,.0f} / {n * cores * count} = {raw:.2f}; normalized {raw:.2f} / "
                          f"({spc.spc:.2f}/{d.spc_source:.2f}) = {norm:.2f}; R_max {rmax:g} -> {status}"),
            inputs={"vcpu_T": round(vcpu_t), "target_cores": n * cores * count, "R_max": rmax}, output=round(norm, 3)))
        if status == "warn":
            warns.append(f"raw vCPU:pCore {raw:.2f} > {rmax:g} but normalized {norm:.2f} OK: pilot to confirm "
                         "CPU ready < 5 %")
    nic, netexpl, netwarn = choose_nics(d.mbps_t, max(n - k, 1), vsan, policy)
    expls.append(netexpl)
    warns += netwarn
    power_host = (cpu.tdp_w or 200) * count + platform.base_power_w
    ru = platform.form_factor_u * n
    if policy.rack_unit_budget and ru > policy.rack_unit_budget:
        reasons.append(f"rack units {ru} > budget {policy.rack_unit_budget}")
    if policy.power_budget_w and power_host * n > policy.power_budget_w:
        reasons.append(f"power {power_host * n:,.0f} W > budget {policy.power_budget_w:,.0f} W")
    if not cpu.sizable:
        reasons.append(f"{cpu.model}: not yet sizable (no SPEC results)")
    capex_h = _capex_host(platform, cpu, count, dimm.total_gb if dimm else 0, dimm.dimm_size_gb if dimm else 0,
                          drives.raw_tb if drives else 0, drives.drive_tb if drives else 0, nic, prices)
    pw = prices.get("power") or {}
    power_opex = (power_host * n / 1000 * 8760 * float(pw.get("avg_load_factor", 0.6))
                  * float(pw.get("pue", 1.5)) * float(pw.get("usd_per_kwh", 0.15)) * d.years)
    raw_inst = (drives.raw_tib if drives else 0) * n
    lines, info = target_licensing(d, n, k, count, cores, cpu.model, d.years, policy, prices, raw_inst, vsan)
    lic_total = sum(line.horizon_usd for line in lines)
    capex = capex_h * n
    tco = capex + capex * float(prices.get("support_pct_per_year_hw", 0.0)) * d.years + power_opex + lic_total
    cfg = TargetConfig(platform=platform.model, cpu_id=cpu.id, cpu_name=cpu.model, cpu_count=count,
                       cores_per_cpu=cores, dimm=dimm, drives=drives, nics=nic,
                       psu=platform.psu_options[-1] if platform.psu_options else None)
    db_cores = info["db_cores"]
    return Candidate(
        config=cfg, label=cfg.label(), spc_target=round(spc.spc, 4), spc_flags=spc.flags,
        spc_confidence=spc.confidence,
        spc_same_system=any(platform.model.lower() in (r.system_name or "").lower().split() for r in spc.results),
        su_host=round(su_host, 3), su_host_effective=round(su_eff, 3),
        n_active=plan.n_active, k_ha=k, n_total=n, n_min_reason=plan.n_min_reason,
        util_normal=round(plan.util_normal, 4), util_failed=round(plan.util_failed, 4),
        mem_per_host_need_gb=round(d.m_t / survivors, 1), dimm=dimm, drives=drives, nics=nic,
        raw_per_host_tib=round(d.storage_raw_tib / n, 3) if n else 0, power_w_host=power_host,
        power_w_total=power_host * n, rack_units=ru, capex_host=round(capex_h), capex_total=round(capex),
        power_opex=round(power_opex), licenses=lines, license_total=round(lic_total), tco=round(tco),
        licensed_cores_vmware=int(info["vmware_cores"]), licensed_cores_db=db_cores,
        oracle_licenses_active=info["oracle_active"], oracle_licenses_all=info["oracle_all"],
        vcpu_ratio_raw=round(raw, 3), vcpu_ratio_norm=round(norm, 3), numa_ok=numa_ok, feasible=not reasons,
        reasons=reasons, warnings=list(dict.fromkeys(warns)), explanations=expls)


def sort_key(c: Candidate, objective: str, db: bool, prefer_measured: bool = True) -> tuple[float, ...]:
    est = 0 if (not prefer_measured or c.spc_confidence.value == "HIGH") else 1
    if db:
        # per-core-licensed DB cluster: optimized on SPC; prefer SPEC measured on that exact platform
        return (c.licensed_cores_db, est, -c.spc_target, c.n_total, 0 if c.spc_same_system else 1, c.tco)
    if objective == "min_hosts":
        return (est, c.n_total, c.tco)
    if objective == "min_licensed_cores":
        return (est, c.licensed_cores_db or c.licensed_cores_vmware, c.tco)
    if objective == "min_power":
        return (est, c.power_w_total, c.tco)
    if objective == "max_headroom":
        return (est, c.util_failed, c.tco)
    return (est, c.tco, c.n_total)


def pareto(cands: list[Candidate]) -> list[Candidate]:
    front = []
    for c in cands:
        if not any((o.n_total <= c.n_total and o.tco <= c.tco) and (o.n_total < c.n_total or o.tco < c.tco)
                   for o in cands):
            front.append(c)
    return sorted(front, key=lambda c: c.n_total)


def enumerate_configs(policy: Policy) -> list[tuple[TargetPlatform, CpuModel, int]]:
    cpus = load_cpus()
    out = []
    for p in load_platforms().values():
        if policy.target_platforms and p.model not in policy.target_platforms:
            continue
        for cid in p.supported_cpu_ids:
            cpu = cpus.get(cid)
            if cpu is None or cpu.max_sockets < p.sockets:
                continue
            out.append((p, cpu, p.sockets))
    return out


def optimize(d: ClusterDemand, policy: Policy, prices: dict[str, Any], cache: BenchCache,
             progress: Callable[[float, str], None] | None = None
             ) -> tuple[list[Candidate], list[Candidate], int, int, list[Candidate]]:
    """Return (top N feasible, pareto front, evaluated, infeasible, all feasible).

    progress(fraction, message) is an optional UI hook; it never affects results."""
    configs = enumerate_configs(policy)
    allc = []
    for i, (p, cpu, n) in enumerate(configs):
        if progress and i % 5 == 0:
            progress(i / max(len(configs), 1), f"{d.cluster}: optimizer {i}/{len(configs)} ({p.model} {cpu.model})")
        allc.append(evaluate(d, p, cpu, n, policy, prices, cache))
    feas = [c for c in allc if c.feasible]
    obj = policy.objective
    pm = bool(getattr(policy, "prefer_measured_spec", True))
    feas.sort(key=lambda c: sort_key(c, obj, d.is_db_cluster, pm))
    pool = [c for c in feas if c.spc_confidence.value == "HIGH"] if pm else feas
    return feas[: policy.top_n], pareto(pool or feas), len(allc), len(allc) - len(feas), feas


def evaluate_named(d: ClusterDemand, platform: str, cpu_id: str, policy: Policy, prices: dict[str, Any],
                   cache: BenchCache) -> Candidate:
    p = load_platforms()[platform]
    return evaluate(d, p, load_cpus()[cpu_id], p.sockets, policy, prices, cache)
