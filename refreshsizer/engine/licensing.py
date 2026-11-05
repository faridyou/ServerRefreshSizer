"""Licensing model (section 5.6): VMware VCF/VVF, Windows Server DC, SQL Server, Oracle, RHEL."""

from __future__ import annotations

import math
from typing import Any

from refreshsizer.catalog.loader import oracle_core_factor
from refreshsizer.config.schema import Policy
from refreshsizer.engine.results import ClusterDemand, LicenseLine
from refreshsizer.model.entities import SourceHost

VMWARE_ORDER_WARNING = ("A 72-core VMware order minimum was reported in 2025 and is disputed: confirm with "
                        "Broadcom/your distributor (vmware_order_min_cores, default 0).")


def _lp(prices: dict[str, Any], key: str, default: float) -> float:
    return float((prices.get("licensing") or {}).get(key, default))


def vmware_lines(n_hosts: int, cpus: int, cores: int, years: int, policy: Policy, prices: dict[str, Any],
                 raw_tib_installed: float, vsan: bool) -> tuple[list[LicenseLine], int]:
    if not policy.target_virtualized or policy.vmware_edition == "none":
        return [], 0
    per_cpu = max(cores, policy.vmware_min_cores_per_cpu)
    lic = n_hosts * cpus * per_cpu
    lic_order = max(lic, policy.vmware_order_min_cores)
    ed = policy.vmware_edition.lower()
    price = _lp(prices, f"vmware_{ed}_usd_per_core_year", 350 if ed == "vcf" else 150)
    lines = [LicenseLine(product=f"VMware {ed.upper()}", metric="cores/yr (min 16 per CPU)", quantity=lic_order,
                         unit_price=price, annual_usd=lic_order * price, horizon_usd=lic_order * price * years,
                         note="reported list, negotiable" + (f"; order minimum {policy.vmware_order_min_cores}"
                                                             if lic_order > lic else ""))]
    if vsan:
        ent = lic * (1.0 if ed == "vcf" else 0.25)
        if raw_tib_installed > ent:
            extra = raw_tib_installed - ent
            p2 = _lp(prices, "vsan_addon_usd_per_tib_year", 200)
            lines.append(LicenseLine(product="vSAN capacity add-on", metric="TiB/yr", quantity=round(extra, 2),
                                     unit_price=p2, annual_usd=extra * p2, horizon_usd=extra * p2 * years,
                                     note=f"raw {raw_tib_installed:.1f} TiB > entitlement {ent:.1f} TiB "
                                          f"({'1' if ed == 'vcf' else '0.25'} TiB/core)"))
    return lines, lic_order


def target_licensing(d: ClusterDemand, n_total: int, k_ha: int, cpus: int, cores: int, cpu_name: str,
                     years: int, policy: Policy, prices: dict[str, Any], raw_tib_installed: float,
                     vsan: bool) -> tuple[list[LicenseLine], dict[str, float]]:
    lines, vmw = vmware_lines(n_total, cpus, cores, years, policy, prices, raw_tib_installed, vsan)
    info: dict[str, float] = {"vmware_cores": vmw, "oracle_active": 0.0, "oracle_all": 0.0, "db_cores": 0.0}
    lic = d.licensing
    host_cores = cpus * cores
    if "windows_dc" in lic:
        per_host = max(16, cpus * max(8, cores))
        q = n_total * per_host
        p = _lp(prices, "windows_dc_usd_per_2core_pack", 850)
        lines.append(LicenseLine(product="Windows Server Datacenter", metric="cores (min 8/CPU, 16/server)",
                                 quantity=q, unit_price=p / 2, one_time_usd=q / 2 * p, horizon_usd=q / 2 * p,
                                 note="all physical cores of every host in the cluster"))
    for ed in ("sql_ent", "sql_std"):
        if ed in lic:
            p = _lp(prices, f"{ed}_usd_per_2core_pack", 15123 if ed == "sql_ent" else 3945)
            sa = _lp(prices, "sql_sa_pct_per_year", 0.25)
            per_host_q = n_total * cpus * max(4, cores)
            per_vm_q = float(lic[ed].get("vm_cores_min4", 4.0 * lic[ed]["workloads"]))
            q = min(per_host_q, per_vm_q)
            mode = "per physical core (all hosts)" if q == per_host_q else "per VM (min 4 cores/VM, requires SA)"
            one = q / 2 * p
            lines.append(LicenseLine(product="SQL Server " + ("Enterprise" if ed == "sql_ent" else "Standard"),
                                     metric=mode, quantity=q, unit_price=p / 2, one_time_usd=one,
                                     annual_usd=one * sa, horizon_usd=one + one * sa * years,
                                     note=f"host-based {per_host_q:g} vs per-VM {per_vm_q:g} cores: cheapest kept"))
            info["db_cores"] += q
    if "oracle_ee" in lic or "oracle_se2" in lic:
        f, f_note = oracle_core_factor(cpu_name)
        per_host = host_cores * f
        active = max(n_total - k_ha, 1)
        info["oracle_active"] = per_host * active
        info["oracle_all"] = per_host * n_total
        use_active = d.is_db_cluster and policy.oracle_db_cluster_licensing == "active_nodes"
        licensed_hosts = active if use_active else n_total
        sup = _lp(prices, "oracle_support_pct_per_year", 0.22)
        if "oracle_ee" in lic:
            q = per_host * licensed_hosts
            p = _lp(prices, "oracle_ee_usd_per_processor", 47500)
            one = q * p
            lines.append(LicenseLine(
                product="Oracle Database EE", metric=f"processor licenses (cores x {f})", quantity=q, unit_price=p,
                one_time_usd=one, annual_usd=one * sup, horizon_usd=one + one * sup * years,
                note=(f"{f_note}; {'active nodes only (failover node, 10-day rule)' if use_active else 'all hosts in the cluster (no hard partitioning under VMware)'};"
                      f" active-only {info['oracle_active']:g}, all nodes {info['oracle_all']:g}")))
            info["db_cores"] += host_cores * licensed_hosts
        if "oracle_se2" in lic:
            q = cpus * licensed_hosts
            p = _lp(prices, "oracle_se2_usd_per_socket", 17500)
            one = q * p
            lines.append(LicenseLine(product="Oracle Database SE2", metric="sockets (max 2 per server)", quantity=q,
                                     unit_price=p, one_time_usd=one, annual_usd=one * sup,
                                     horizon_usd=one + one * sup * years))
            info["db_cores"] += host_cores * licensed_hosts
    if "rhel" in lic:
        q = n_total * math.ceil(cpus / 2)
        p = _lp(prices, "rhel_usd_per_socket_pair_year", 1300)
        lines.append(LicenseLine(product="RHEL", metric="socket-pair subscriptions/yr", quantity=q, unit_price=p,
                                 annual_usd=q * p, horizon_usd=q * p * years))
    return lines, info


def source_licensing(d: ClusterDemand, hosts: list[SourceHost], workloads_vp: float, years: int,
                     policy: Policy, prices: dict[str, Any]) -> list[LicenseLine]:
    """Same metrics on the source estate, for the licensing delta."""
    lines: list[LicenseLine] = []
    if d.platform_type == "x86":
        cores = 0
        for h in hosts:
            s = h.sockets or 2
            cps = h.cores_per_socket or ((h.cores or 0) // s if h.cores else 0)
            cores += s * max(cps, policy.vmware_min_cores_per_cpu)
        if policy.vmware_edition != "none" and any((h.hypervisor or "") == "vSphere" for h in hosts):
            price = _lp(prices, f"vmware_{policy.vmware_edition}_usd_per_core_year", 350)
            lines.append(LicenseLine(product=f"VMware {policy.vmware_edition.upper()} (if renewed on source)",
                                     metric="cores/yr", quantity=cores, unit_price=price, annual_usd=cores * price,
                                     horizon_usd=cores * price * years))
    if "oracle_ee" in d.licensing:
        if d.platform_type == "POWER":
            q = workloads_vp * 1.0
            note = "PowerVM LPAR (hard partitioning): licensed on LPAR virtual processors x 1.0"
        else:
            tot = sum(h.cores or 0 for h in hosts)
            f, _ = oracle_core_factor(hosts[0].cpu_model if hosts else None)
            q = tot * f
            note = f"all source cores x {f}"
        p = _lp(prices, "oracle_ee_usd_per_processor", 47500)
        sup = _lp(prices, "oracle_support_pct_per_year", 0.22)
        lines.append(LicenseLine(product="Oracle Database EE (source)", metric="processor licenses", quantity=q,
                                 unit_price=p, annual_usd=q * p * sup, horizon_usd=q * p * sup * years,
                                 note=note + "; support only (licenses already owned)"))
    return lines
