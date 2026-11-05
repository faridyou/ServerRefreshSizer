"""Constraint checks: NUMA, vCPU:pCore (raw and performance-normalized), power/RU budgets (section 7.8)."""

from __future__ import annotations

from refreshsizer.config.schema import Policy
from refreshsizer.model.entities import Explanation


def ratio_max(criticality: str, wl_class: str, policy: Policy) -> float:
    r = policy.vcpu_ratio_max
    if criticality == "tier0":
        return float(r.get("tier0", 4))
    if criticality == "tier1":
        return float(r.get("tier1", 6))
    if criticality == "tier3" or wl_class == "vdi":
        return float(r.get("dev", 10))
    return float(r.get("general", 8))


def numa_check(largest_vcpu: float, cores_per_cpu: int, policy: Policy) -> tuple[bool, Explanation]:
    node = cores_per_cpu / max(policy.numa_nps, 1)
    ok = largest_vcpu <= node
    return ok, Explanation(step_id="numa", title="NUMA fit of the largest VM",
                           formula_text=f"largest VM {largest_vcpu:g} vCPU <= cores per NUMA node {node:g} "
                                        f"(NPS{policy.numa_nps}) -> {'OK' if ok else 'VIOLATION'}",
                           inputs={"largest_vm_vcpu": largest_vcpu, "cores_per_numa_node": node},
                           output=ok)


def vcpu_ratio(vcpu_t: float, target_cores: int, spc_t: float, spc_s: float, rmax: float) -> tuple[float, float, str]:
    raw = vcpu_t / target_cores if target_cores else 0.0
    perf = (spc_t / spc_s) if spc_s else 1.0
    norm = raw / perf if perf else raw
    if raw <= rmax:
        status = "ok"
    elif norm <= rmax:
        status = "warn"
    else:
        status = "fail"
    return raw, norm, status
