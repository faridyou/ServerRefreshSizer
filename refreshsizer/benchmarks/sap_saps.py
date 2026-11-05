"""Workload-specific curated benchmarks (SAP SD, VMmark 4, SPECjbb2015, TPC-E) and k_wl helper."""

from __future__ import annotations

from refreshsizer.benchmarks.cache import BenchCache
from refreshsizer.model.entities import BenchmarkResult

WORKLOAD_SUITES = ("saps", "specjbb2015", "vmmark4", "tpce", "cpu2017_fp")


def workload_results(cache: BenchCache, suite: str) -> list[BenchmarkResult]:
    return cache.query(None, suite)


def per_core(cache: BenchCache, suite: str, cpu_id: str | None) -> float | None:
    if not cpu_id:
        return None
    vals = [r.value_base / r.enabled_cores for r in cache.query(cpu_id, suite)
            if r.value_base and r.enabled_cores]
    return sum(vals) / len(vals) if vals else None


def k_wl(cache: BenchCache, suite: str, src_cpu: str | None, tgt_cpu: str | None,
         spc_src: float, spc_tgt: float) -> tuple[float, str]:
    """k_wl = (workload ratio source/target) / (SPC ratio source/target), both per core.

    k_wl < 1 when the target does better on the workload benchmark than SPEC CPU suggests."""
    a, b = per_core(cache, suite, src_cpu), per_core(cache, suite, tgt_cpu)
    if a and b and spc_src and spc_tgt:
        k = (a / b) / (spc_src / spc_tgt)
        return k, f"{suite}: per-core ratio src/tgt {a / b:.3f} / SPC ratio src/tgt {spc_src / spc_tgt:.3f}"
    return 1.0, f"{suite}: not available on both sides; k_wl = 1.0"
