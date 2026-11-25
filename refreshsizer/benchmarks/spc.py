"""SPC (SPECrate2017_int_base per core) resolution with the documented fallback chain (section 5.1)."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from refreshsizer.benchmarks.cache import BenchCache
from refreshsizer.benchmarks.cpu_matcher import match_cpu
from refreshsizer.catalog.loader import load_cpus
from refreshsizer.model.entities import BenchmarkResult, Confidence


@dataclass
class SpcResult:
    cpu_id: str | None
    cpu_name: str
    spc: float
    spc_min: float
    spc_max: float
    method: str  # exact | scaled_chips | sibling | user | generation_avg | default
    confidence: Confidence
    flags: list[str] = field(default_factory=list)
    results: list[BenchmarkResult] = field(default_factory=list)
    chips: int | None = None

    def refs(self) -> list[str]:
        out = []
        for r in self.results:
            out.append(f"{r.metric} {r.value_base:g} ({r.enabled_cores} cores, {r.chips} chips) - "
                       f"{r.system_name} - sponsor {r.sponsor or 'n/a'} - tested {r.test_date or 'n/a'} - "
                       f"{r.result_url} [{r.source}, retrieved {r.retrieved_at}]")
        return out


def _year(r: BenchmarkResult) -> str:
    return (r.test_date or "")[:4]


def _dedupe(results: list[BenchmarkResult]) -> list[BenchmarkResult]:
    live = [r for r in results if r.source == "live"]
    if not live:
        return results
    keys = {((r.system_name or "").lower(), r.chips) for r in live}
    return [r for r in results if r.source != "curated" or ((r.system_name or "").lower(), r.chips) not in keys]


def _pick(results: list[BenchmarkResult], chips: int | None, system_hint: str | None) -> tuple[list[BenchmarkResult], bool]:
    """Preference: same Dell model, same chip count, newest software stack, median of the rest."""
    cands = [r for r in results if r.per_core]
    user = [r for r in cands if r.source == "user"]
    if user:
        cands = user
    if system_hint:
        hint = system_hint.lower().replace("poweredge", "").strip()
        same = [r for r in cands if hint and hint in (r.system_name or "").lower()]
        if same:
            cands = same
    same_chips = True
    if chips is not None:
        sc = [r for r in cands if r.chips == chips]
        if sc:
            cands = sc
        else:
            same_chips = False
    if len(cands) > 1:
        newest = max(_year(r) for r in cands)
        cands = [r for r in cands if _year(r) == newest]
    return cands, same_chips


CPU2017_RELEASE = "2017-06"


def _pre_release(r: BenchmarkResult) -> bool:
    d = str(r.test_date or "")
    return bool(d) and d[:7] < CPU2017_RELEASE


def _sibling(cpu_id: str | None, name: str, chips: int | None, system_hint: str | None, cache: BenchCache,
             suite: str, metric: str) -> tuple[float, str, list[BenchmarkResult]] | None:
    """Same-generation SKU with a result (closest core count, then highest clock), scaled by the clock ratio."""
    cpus = load_cpus()
    if not cpu_id or cpu_id not in cpus:
        return None
    me = cpus[cpu_id]
    sibs = [c for c in cpus.values() if c.codename == me.codename and c.id != me.id]
    for sc in sorted(sibs, key=lambda c: (abs(c.cores - me.cores), -c.base_ghz)):
        rs = [r for r in _dedupe(cache.query(sc.id, suite, metric)) if not _pre_release(r)] or \
            _dedupe(cache.query(sc.id, suite, metric))
        if not rs:
            continue
        picked, _ = _pick(rs, chips, system_hint)
        base_spc = statistics.median([r.per_core or 0 for r in picked])
        ghz_me = me.allcore_ghz or me.base_ghz
        ghz_s = sc.allcore_ghz or sc.base_ghz
        ratio = (ghz_me / ghz_s) if ghz_me and ghz_s else 1.0
        spc = base_spc * ratio
        note = (f"ESTIMATE: sibling {sc.model} SPC {base_spc:.2f} x clock ratio {ghz_me}/{ghz_s} GHz = {spc:.2f} "
                f"per core, applied to the {me.cores} cores of {name}")
        return spc, note, picked
    return None


def resolve_spc(cpu: str | None, chips: int | None = None, system_hint: str | None = None,
                cache: BenchCache | None = None, user_spc: dict[str, float] | None = None,
                suite: str = "cpu2017", metric: str = "SPECrate2017_int_base") -> SpcResult:
    from refreshsizer.benchmarks.curated_db import ensure_db

    cache = cache or ensure_db()
    cpus = load_cpus()
    cpu_id = None
    name = cpu or "unknown CPU"
    if cpu:
        if cpu in cpus:
            cpu_id = cpu
        else:
            m = match_cpu(cpu)
            cpu_id = m.cpu_id if m else None
        if cpu_id and cpu_id in cpus:
            name = cpus[cpu_id].model
    flags: list[str] = []

    # (0/a) exact CPU results (user > live > curated), possibly another socket count
    if cpu_id:
        all_r = _dedupe(cache.query(cpu_id, suite, metric))
        if all_r:
            picked, same_chips = _pick(all_r, chips, system_hint)
            if picked:
                vals = [r.per_core or 0 for r in picked]
                spc = statistics.median(vals)
                every = [r.per_core or 0 for r in all_r if r.per_core]
                lo, hi = min(every), max(every)
                if lo == hi:
                    lo, hi = spc * 0.97, spc * 1.03
                    flags.append("single matching result: SPEC variance band +/-3 %")
                method, conf = "exact", Confidence.HIGH
                if not same_chips:
                    method, conf = "scaled_chips", Confidence.MEDIUM
                    flags.append(f"FLAG: no {chips}-socket result for {name}; scaled by cores from "
                                 f"{picked[0].chips}-socket result")
                if any(r.source == "user" for r in picked):
                    method = "user"
                elif all(_pre_release(r) for r in picked):
                    # results tested before the SPEC CPU2017 release used early compilers and tend to score low;
                    # sizing on a too-low source SPC would shrink the measured demand (undersizing), so the
                    # same-generation estimate is used when it is higher; the published result stays listed.
                    sib = _sibling(cpu_id, name, chips, system_hint, cache, suite, metric)
                    if sib and sib[0] > spc:
                        est, note, basis = sib
                        flags.append(f"published {name} result(s) tested before the SPEC CPU2017 release (June 2017) "
                                     f"give SPC {spc:.2f}, below the same-generation estimate {est:.2f} ({note}); "
                                     f"the higher value {est:.2f} is used (conservative: avoids undersizing)")
                        return SpcResult(cpu_id, name, est, spc, max(hi, est), "exact_pre_release",
                                         Confidence.MEDIUM, flags, picked + basis, chips)
                return SpcResult(cpu_id, name, spc, lo, hi, method, conf, flags, picked, chips)

    # (b) sibling SKU of the same generation, scaled by the clock ratio
    sib = _sibling(cpu_id, name, chips, system_hint, cache, suite, metric)
    if sib:
        spc, note, picked = sib
        flags.append(note)
        return SpcResult(cpu_id, name, spc, spc * 0.9, spc * 1.1, "sibling", Confidence.MEDIUM, flags, picked, chips)

    # (c) user-provided value
    if user_spc:
        for key in (cpu_id, cpu, name):
            if key and key in user_spc:
                v = float(user_spc[key])
                flags.append(f"user-provided SPC {v} for {name}")
                return SpcResult(cpu_id, name, v, v * 0.95, v * 1.05, "user", Confidence.MEDIUM, flags, [], chips)

    # (d) curated DB is already part of the exact lookup; (e) generation average
    gen = cache.get_meta("generation_avg_spc", {}) or {}
    if cpu_id and cpu_id in cpus and cpus[cpu_id].codename in gen:
        v = float(gen[cpus[cpu_id].codename])
        flags.append(f"LOW confidence: generation-average SPC {v} for {cpus[cpu_id].codename} (no result for {name})")
        return SpcResult(cpu_id, name, v, v * 0.85, v * 1.15, "generation_avg", Confidence.LOW, flags, [], chips)
    v = float(gen.get("x86_unknown", 5.0))
    flags.append(f"VERY LOW confidence: CPU '{cpu}' unknown; default x86 SPC {v} used (ASSUMPTION)")
    return SpcResult(cpu_id, name, v, v * 0.7, v * 1.3, "default", Confidence.VERY_LOW, flags, [], chips)
