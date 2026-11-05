"""Build/refresh data/benchmarks.sqlite from the curated YAML seeds (offline database)."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import yaml

from refreshsizer.benchmarks.cache import BenchCache
from refreshsizer.benchmarks.cpu_matcher import match_cpu
from refreshsizer.model.entities import BenchmarkResult
from refreshsizer.paths import SEEDS_DIR

log = logging.getLogger("refreshsizer.bench")
CURATED_URL = "spec.org (curated, URL to be refreshed)"
CURATED_DATE = "2026-01-01 (curated seed)"


def _seed(name: str) -> dict[str, Any]:
    with open(SEEDS_DIR / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def seeds_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(SEEDS_DIR.glob("*.yaml")):
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def curated_results() -> list[BenchmarkResult]:
    out: list[BenchmarkResult] = []
    spec = _seed("spec_cpu2017.yaml")
    for r in spec.get("results", []):
        m = match_cpu(r["cpu"])
        out.append(BenchmarkResult(
            id=r["id"], suite=spec.get("suite", "cpu2017"), metric=spec.get("metric", "SPECrate2017_int_base"),
            value_base=float(r["base"]), system_vendor=r.get("vendor"), system_name=r.get("system"),
            cpu_name=r["cpu"], cpu_model_id=m.cpu_id if m else None, chips=r.get("chips"),
            enabled_cores=r.get("cores"), threads_per_core=r.get("tpc"), test_date=str(r.get("test_date", "")),
            sponsor=r.get("sponsor"), software=r.get("software"), result_url=r.get("url") or CURATED_URL,
            retrieved_at=CURATED_DATE, source="curated", notes=r.get("notes"),
            smt_mode=r.get("tpc") if "IBM" in str(r.get("vendor")) else None,
        ))
    wl = _seed("workload_benchmarks.yaml")
    for r in wl.get("results", []):
        m = match_cpu(r["cpu"])
        out.append(BenchmarkResult(
            id=r["id"], suite=r["suite"], metric=r["metric"], value_base=float(r["value"]),
            system_name=r.get("system"), cpu_name=r["cpu"], cpu_model_id=m.cpu_id if m else None,
            chips=r.get("chips"), enabled_cores=r.get("cores"), result_url=r.get("url") or CURATED_URL,
            retrieved_at=CURATED_DATE, source="curated", notes=r.get("notes")))
    return out


def generation_avg_spc() -> dict[str, float]:
    return {str(k): float(v) for k, v in (_seed("spec_cpu2017.yaml").get("generation_avg_spc") or {}).items()}


def build(cache: BenchCache | None = None, force: bool = False) -> BenchCache:
    cache = cache or BenchCache()
    h = seeds_hash()
    if force or cache.get_meta("curated_hash") != h or cache.count("curated") == 0:
        cache.conn.execute("DELETE FROM results WHERE source='curated'")
        n = cache.upsert(curated_results(), overwrite=False)
        cache.set_meta("curated_hash", h)
        cache.set_meta("generation_avg_spc", generation_avg_spc())
        cache.log("INFO", f"curated DB built: {n} results")
        log.info("curated benchmark DB built (%d results) at %s", n, cache.path)
    return cache


_OPEN: dict[str, BenchCache] = {}


def ensure_db(path: Path | None = None) -> BenchCache:
    """Open (and build if needed) the benchmark DB. Never touches the network."""
    cache = BenchCache(path)
    key = str(cache.path)
    if key in _OPEN:
        cache.close()
        return _OPEN[key]
    build(cache)
    _OPEN[key] = cache
    return cache
