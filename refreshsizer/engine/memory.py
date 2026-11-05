"""Memory demand and per-host DIMM selection (section 7.9)."""

from __future__ import annotations

from refreshsizer.catalog.dimm_rules import choose_dimms
from refreshsizer.config.schema import Policy
from refreshsizer.engine.growth import growth_factor
from refreshsizer.model.entities import DimmConfig, Explanation, SizingMode, SourceHost, SourceWorkload, TargetPlatform


def memory_demand(workloads: list[SourceWorkload], hosts: list[SourceHost], share: float, policy: Policy,
                  mode: SizingMode, years: int, growth_pct: float) -> tuple[str, float, float, Explanation, list[str]]:
    flags: list[str] = []
    basis = policy.memory_basis
    conf_vals = [w.ram_gb_configured for w in workloads if w.ram_gb_configured is not None]
    m0 = 0.0
    used_basis = basis
    if basis == "consumed" and any(w.ram_gb_consumed is not None for w in workloads):
        m0 = sum(w.ram_gb_consumed or w.ram_gb_configured or 0 for w in workloads)
    elif basis == "active" and any(w.ram_gb_active is not None for w in workloads):
        m0 = sum((w.ram_gb_active or 0) for w in workloads) * policy.active_memory_factor
        used_basis = f"active x {policy.active_memory_factor}"
    elif basis == "overcommit" and conf_vals:
        m0 = sum(conf_vals) / policy.memory_overcommit_ratio
        used_basis = f"configured / overcommit {policy.memory_overcommit_ratio}"
    elif conf_vals:
        m0 = sum(conf_vals)
        used_basis = "configured"
        if basis != "configured":
            flags.append(f"memory basis '{basis}' not available; configured VM memory used")
    else:
        used = [h.mem_used_gb for h in hosts if h.mem_used_gb is not None]
        if used and mode != SizingMode.INVENTORY_ONLY:
            m0 = sum(used) * share
            used_basis = "host memory used (peak)"
            flags.append("ASSUMPTION: no VM memory; host used/peak memory taken as basis")
        else:
            m0 = sum(h.ram_gb or 0 for h in hosts) * share
            used_basis = "installed host RAM"
            flags.append("ASSUMPTION: no VM memory; installed host RAM taken as basis (conservative)")
    f = growth_factor(growth_pct, years)
    events = sum(e.mem_gib for e in policy.growth_events if e.year <= years)
    o, r = policy.hypervisor_mem_overhead, policy.mem_free_reserve
    m_t = (m0 * f + events) * (1 + o) / (1 - r)
    expl = Explanation(
        step_id="memory", title="Memory requirement",
        formula_latex=r"M_T = M_0 (1+g_m)^T (1+o_{hyp}) / (1-r_{free})",
        formula_text=f"{m0:,.1f} x (1 + {growth_pct:g} %)^{years} x (1 + {o:g}) / (1 - {r:g}) = {m_t:,.1f} GB",
        inputs={"M0_GB": round(m0, 2), "basis": used_basis, "g_m_pct": growth_pct, "T": years, "o_hyp": o,
                "r_free": r, "events_GB": events},
        output=round(m_t, 2), unit="GB", assumptions=flags)
    return used_basis, m0, m_t, expl, flags


def host_memory(platform: TargetPlatform, cpu_count: int, cores_per_cpu: int, core_type: str,
                cpu_max_mt_s: int | None, need_gb: float) -> DimmConfig | None:
    return choose_dimms(platform, cpu_count, cores_per_cpu, core_type, cpu_max_mt_s, need_gb)
