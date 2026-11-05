"""Source capacity in SPEC Units: SU_cap(h) = SPC(cpu) x cores(h); POWER via rPerf x F (section 7.1)."""

from __future__ import annotations

from dataclasses import dataclass, field

from refreshsizer.benchmarks.cache import BenchCache
from refreshsizer.benchmarks.ibm_rperf import rperf_factor, rperf_per_core
from refreshsizer.benchmarks.spc import SpcResult, resolve_spc
from refreshsizer.catalog.loader import load_cpus
from refreshsizer.config.schema import Policy
from refreshsizer.model.entities import Confidence, SourceHost


@dataclass
class Capacity:
    su_cap: float
    spc: float
    spc_min: float
    spc_max: float
    cores: int
    cpu_name: str
    method: str
    confidence: Confidence
    flags: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    rperf_per_core: float | None = None
    rperf_factor: float | None = None
    rperf_factor_matched: float | None = None
    generation: str | None = None


def host_cores(h: SourceHost, flags: list[str]) -> int:
    if h.cores:
        return int(h.cores)
    from refreshsizer.benchmarks.cpu_matcher import match_cpu

    m = match_cpu(h.cpu_model)
    cpus = load_cpus()
    if m and m.cpu_id in cpus:
        sockets = h.sockets or 2
        if not h.sockets:
            flags.append(f"ASSUMPTION: {h.host_id}: socket count unknown, assumed 2")
        c = cpus[m.cpu_id].cores * sockets
        flags.append(f"ASSUMPTION: {h.host_id}: cores derived from CPU catalog ({sockets} x {cpus[m.cpu_id].cores})")
        return c
    flags.append(f"ASSUMPTION: {h.host_id}: core count unknown, assumed 16")
    return 16


def power_capacity(model: str | None, smt: int | None, cores: int | None, policy: Policy) -> Capacity | None:
    info = rperf_per_core(model, smt)
    if not info:
        return None
    f, matched, note = rperf_factor(info.generation, policy.rperf_factor_mode, policy.rperf_to_spec_default)
    c = int(cores or info.cores)
    spc_equiv = info.rperf_per_core * f
    lo = info.rperf_per_core * 0.365
    hi = info.rperf_per_core * 0.425
    flags = list(info.flags) + [f"cross-architecture conversion: rPerf x F ({note})"]
    return Capacity(
        su_cap=spc_equiv * c, spc=spc_equiv, spc_min=min(lo, spc_equiv), spc_max=max(hi, spc_equiv), cores=c,
        cpu_name=f"{info.model} ({info.generation}, SMT{info.smt_used})", method="rperf",
        confidence=Confidence.MEDIUM, flags=flags,
        refs=[f"IBM rPerf {info.model} SMT{info.smt_used} = {info.rperf_system} ({info.cores} cores) - {info.url}"],
        rperf_per_core=info.rperf_per_core, rperf_factor=f, rperf_factor_matched=matched, generation=info.generation)


def host_capacity(h: SourceHost, policy: Policy, cache: BenchCache) -> Capacity:
    flags: list[str] = list(h.assumptions)
    if h.platform_type == "POWER":
        cap = power_capacity(h.rperf_system or h.model or h.cpu_model, h.smt_mode, h.cores, policy)
        if cap:
            cap.flags = flags + cap.flags
            return cap
        flags.append(f"ESTIMATE: {h.host_id}: POWER system '{h.model}' not in rPerf table; curated SPEC used")
    cores = host_cores(h, flags)
    user_spc = getattr(policy, "user_spc", None)
    spc: SpcResult = resolve_spc(h.cpu_model, chips=h.sockets, system_hint=h.model, cache=cache,
                                 user_spc=user_spc if isinstance(user_spc, dict) else None)
    return Capacity(su_cap=spc.spc * cores, spc=spc.spc, spc_min=spc.spc_min, spc_max=spc.spc_max, cores=cores,
                    cpu_name=spc.cpu_name, method=spc.method, confidence=spc.confidence, flags=flags + spc.flags,
                    refs=spc.refs())
