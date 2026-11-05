"""Storage demand (capacity, IOPS, throughput) and per-host drive choice (section 7.10)."""

from __future__ import annotations

import math
from typing import Any

from refreshsizer.catalog.loader import load_drives
from refreshsizer.config.schema import Policy
from refreshsizer.engine.growth import growth_factor
from refreshsizer.model.entities import (
    DriveConfig,
    Explanation,
    SizingMode,
    SourceHost,
    SourceWorkload,
    StorageArray,
)

P_PROT = {"vsan_esa_raid1": 2.0, "vsan_esa_raid5_4p1": 1.25, "vsan_esa_raid5_2p1": 1.5, "vsan_esa_raid6_4p2": 1.5,
          "external": 1.0}
MIN_HOSTS = {"vsan_esa_raid1": 3, "vsan_esa_raid5_4p1": 4, "vsan_esa_raid5_2p1": 3, "vsan_esa_raid6_4p2": 6,
             "external": 2}
TB_TO_TIB = 0.9095


def resolve_protection(policy: Policy, external: bool) -> tuple[str, str]:
    if external:
        return "external", "external SAN/array storage (vSAN host minimums do not apply)"
    prot = policy.protection
    cap = policy.max_hosts_per_cluster_user
    if prot == "vsan_esa_raid5_4p1" and cap is not None and cap <= 3:
        return "vsan_esa_raid5_2p1", "user caps cluster at 3 hosts: RAID-5 ESA switched to 2+1"
    return prot, f"{prot} (min {MIN_HOSTS.get(prot, 2)} hosts)"


def storage_demand(workloads: list[SourceWorkload], hosts: list[SourceHost], arrays: list[StorageArray],
                   share: float, policy: Policy, mode: SizingMode, years: int, protection: str) -> dict[str, Any]:
    flags: list[str] = []
    by_class: dict[str, float] = {}
    inventory = mode == SizingMode.INVENTORY_ONLY
    for w in workloads:
        v = (w.storage_provisioned_gib if inventory else w.storage_used_gib)
        if v is None:
            v = w.storage_used_gib if inventory else w.storage_provisioned_gib
            if v is not None and not inventory:
                flags.append("ASSUMPTION: storage used missing for some workloads; provisioned used")
        if v is not None:
            by_class[w.workload_class] = by_class.get(w.workload_class, 0.0) + v / 1024.0
    basis = "provisioned (inventory-only)" if inventory else "used"
    if not by_class and arrays:
        tot = sum((a.used_tib or 0) for a in arrays) * share
        if tot:
            by_class["default"] = tot
            basis = "array/datastore used"
    if not by_class:
        flags.append("ASSUMPTION: no storage capacity data; storage sized at 0 - customer question raised")
    used0 = sum(by_class.values())
    g = policy.growth.storage_pct
    f = growth_factor(g, years)
    events = sum(e.storage_tib for e in policy.growth_events if e.year <= years)
    used_t = used0 * f + events
    eff = sum(v * f / policy.drr_for(c) for c, v in by_class.items()) + events / policy.drr_for("default")
    drr_eff = (used_t / eff) if eff else policy.drr_for("default")
    pp = P_PROT.get(protection, 1.25)
    raw = eff * pp / (1 - policy.slack)
    expl = Explanation(
        step_id="storage", title="Storage capacity",
        formula_latex=r"Raw = \frac{Used_0 (1+g_s)^T / DRR \times P_{prot}}{1 - slack}",
        formula_text=(f"{used0:,.2f} TiB x (1 + {g:g} %)^{years} = {used_t:,.2f} TiB; / DRR {drr_eff:.2f} = "
                      f"{eff:,.2f} TiB; x {pp} / (1 - {policy.slack}) = {raw:,.2f} TiB raw"),
        inputs={"Used0_TiB": round(used0, 3), "basis": basis, "g_s_pct": g, "T": years, "DRR": round(drr_eff, 3),
                "P_prot": pp, "protection": protection, "slack": policy.slack, "by_class_TiB":
                {k: round(v, 2) for k, v in by_class.items()}},
        output=round(raw, 3), unit="TiB", assumptions=flags)
    # IOPS
    iops0 = sum((w.iops_p95 or w.iops_peak or 0) for w in workloads)
    iops_basis = "workload p95/peak IOPS"
    if not iops0:
        iops0 = sum((h.iops_p95 or h.iops_peak or 0) for h in hosts) * share
        iops_basis = "host p95/peak IOPS"
    if not iops0:
        iops0 = sum((a.iops_p95 or 0) for a in arrays) * share
        iops_basis = "array p95 IOPS"
    if not iops0:
        iops0 = sum(policy.iops_for(w.workload_class) for w in workloads)
        iops_basis = "ASSUMPTION: iops_profile_per_class"
        if workloads:
            flags.append("ASSUMPTION: no IOPS data; per-class IOPS profile used")
    reads = [w.read_pct for w in workloads if w.read_pct is not None]
    read_pct = sum(reads) / len(reads) if reads else 70.0
    gi = policy.growth.iops_pct
    iops_t = iops0 * growth_factor(gi, years)
    pen = float(policy.write_penalty.get(protection, 2))
    backend = iops_t * read_pct / 100 + iops_t * (1 - read_pct / 100) * pen
    iexpl = Explanation(
        step_id="iops", title="IOPS (front-end and back-end)",
        formula_latex=r"IOPS_{be} = R + W \times penalty",
        formula_text=(f"{iops0:,.0f} x (1 + {gi:g} %)^{years} = {iops_t:,.0f} front-end; read {read_pct:.0f} %, "
                      f"write penalty {pen:g} -> {backend:,.0f} back-end IOPS"),
        inputs={"IOPS0": round(iops0), "basis": iops_basis, "read_pct": read_pct, "penalty": pen},
        output=round(backend), unit="IOPS")
    mbps0 = sum((h.throughput_mbps_peak or 0) for h in hosts) * share
    if not mbps0:
        mbps0 = sum((w.throughput_mbps_p95 or 0) for w in workloads)
    if not mbps0:
        mbps0 = sum((a.mbps_p95 or 0) for a in arrays) * share
    mbps_t = mbps0 * growth_factor(gi, years)
    return {"used0": used0, "used_t": used_t, "effective": eff, "raw": raw, "p_prot": pp, "drr": drr_eff,
            "iops0": iops0, "iops_t": iops_t, "backend": backend, "read_pct": read_pct, "mbps0": mbps0,
            "mbps_t": mbps_t, "explanations": [expl, iexpl], "flags": flags}


def choose_drives(raw_per_host_tib: float, bays: int, prices: dict[str, Any], min_drives: int) -> DriveConfig | None:
    """Cheapest drive set (price/TB table) that fits the bays; tie -> fewest drives."""
    if raw_per_host_tib <= 0:
        return None
    sizes = [float(d["tb"]) for d in load_drives().get("drives", [])]
    price = {float(k): float(v) for k, v in (prices.get("drive_usd_per_tb") or {}).items()}
    best: tuple[float, int, DriveConfig] | None = None
    need_tb = raw_per_host_tib / TB_TO_TIB
    for tb in sizes:
        n = max(min_drives, math.ceil(need_tb / tb - 1e-9))
        if n > bays:
            continue
        cost = n * tb * price.get(tb, 120.0)
        cfg = DriveConfig(drive_tb=tb, count=n, raw_tb=round(n * tb, 2), raw_tib=round(n * tb * TB_TO_TIB, 3))
        key = (cost, n)
        if best is None or key < (best[0], best[1]):
            best = (cost, n, cfg)
    return best[2] if best else None
