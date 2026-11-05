"""Sizing orchestrator: per cluster (and per-core-licensed DB split) demand -> optimizer -> extras."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from refreshsizer.benchmarks.cache import BenchCache
from refreshsizer.benchmarks.curated_db import ensure_db
from refreshsizer.catalog.loader import load_cpus
from refreshsizer.config.schema import Policy, load_prices
from refreshsizer.engine import uncertainty
from refreshsizer.engine.demand import LICENSED, compute_demand
from refreshsizer.engine.licensing import VMWARE_ORDER_WARNING, source_licensing
from refreshsizer.engine.modes import select_mode
from refreshsizer.engine.naive import naive
from refreshsizer.engine.optimizer import optimize
from refreshsizer.engine.results import ClusterDemand, ClusterSizing, SizingRun
from refreshsizer.model.entities import Cluster, Project, SizingMode, SourceWorkload

log = logging.getLogger("refreshsizer.engine")

DISCLAIMERS = [
    "SPEC(R), SPECrate(R) and SPEC CPU(R) are trademarks of the Standard Performance Evaluation Corporation. "
    "All comparisons in this report are estimates made by RefreshSizer, not results or statements of SPEC. "
    "Base metrics (SPECrate2017_int_base) are used throughout; peak metrics are never used for sizing.",
    "Benchmark result URLs, test sponsors and systems are cited in the Benchmark basis section.",
    "IBM rPerf values are IBM estimates; the rPerf -> SPEC factor is RefreshSizer's estimate.",
    "Prices are editable placeholders (config/prices.yaml), not quotes. The Dell spec sheet/configurator is "
    "authoritative for platform configurations.",
    VMWARE_ORDER_WARNING,
]


def split_cluster(c: Cluster, policy: Policy) -> list[tuple[str, list[SourceWorkload], float, bool]]:
    lic = [w for w in c.workloads if w.licensing in LICENSED]
    split = policy.split_clusters
    if split in (False, "false", "no", "off") or not lic:
        return [(c.name, c.workloads, 1.0, False)]
    if len(lic) == len(c.workloads):
        return [(c.name, c.workloads, 1.0, True)]
    rest = [w for w in c.workloads if w.licensing not in LICENSED]
    tot = sum(w.vcpu or 1 for w in c.workloads) or 1
    s_db = sum(w.vcpu or 1 for w in lic) / tot
    return [(f"{c.name}-db", lic, s_db, True), (c.name, rest, 1 - s_db, False)]


def size_cluster(c: Cluster, policy: Policy, cache: BenchCache, prices: dict[str, Any]) -> list[ClusterSizing]:
    mode, _, why = select_mode(c)
    out = []
    for name, wls, share, is_db in split_cluster(c, policy):
        d: ClusterDemand = compute_demand(c, mode, policy, cache, name=name, workloads=wls, share=share,
                                          is_db_cluster=is_db)
        d.flags.insert(0, f"mode {d.mode.value}: {why}")
        top, front, n_eval, n_inf, feas = optimize(d, policy, prices, cache)
        cs = ClusterSizing(demand=d, candidates=top, pareto=front, objective="db: min licensed cores, max SPC"
                           if is_db else policy.objective, evaluated=n_eval, infeasible=n_inf)
        if top:
            rec = top[0]
            cs.recommended = rec
            cpu = load_cpus()[rec.config.cpu_id]
            cs.naive = naive(d, rec.config.total_cores, cpu.base_ghz, policy)
            cs.uncertainty = uncertainty.run(d, rec, policy)
            if d.mode == SizingMode.INVENTORY_ONLY:
                from refreshsizer.engine.optimizer import evaluate_named

                d2 = d.model_copy(update={"apply_headroom": not d.apply_headroom,
                                          "c_req": d.d_t / d.u_max if not d.apply_headroom else d.d_t})
                cs.inventory_no_headroom_hosts = evaluate_named(d2, rec.config.platform, rec.config.cpu_id, policy,
                                                                prices, cache).n_total
                for row in d.inventory_sensitivity:
                    d3 = d.model_copy(update={"c_req": row["c_req"], "d_t": row["d_t"]})
                    row["hosts"] = evaluate_named(d3, rec.config.platform, rec.config.cpu_id, policy, prices,
                                                  cache).n_total
            if cs.uncertainty.p90_hosts > rec.n_total:
                cs.recommendations.append(
                    f"Monte Carlo P90 = {cs.uncertainty.p90_hosts} hosts (> deterministic {rec.n_total}): "
                    f"recommend P90 = {cs.uncertainty.p90_hosts} hosts for the final BoM")
        else:
            cs.warnings.append("no feasible configuration found; relax constraints or split the cluster")
        vp = sum(w.vcpu or 0 for w in wls if w.physc_p95 is not None)
        cs.source_licensing = source_licensing(d, c.hosts, vp, d.years, policy, prices)
        cs.recommendations += validation_plan(d)
        out.append(cs)
    return out


def validation_plan(d: ClusterDemand) -> list[str]:
    rec = []
    if d.mode in (SizingMode.SNAPSHOT, SizingMode.INVENTORY_ONLY):
        rec.append("Run a 7-30 day Dell Live Optics collection before the final BoM (low-confidence input data).")
    if d.platform_type != "x86":
        rec.append("Cross-architecture migration: proof of concept on the target platform required.")
    if d.wl_class in ("oltp_db", "dwh") or d.is_db_cluster:
        rec.append("Database validation: HammerDB TPROC-C (OLTP) and TPROC-H (analytics) on the pilot host.")
    rec.append("Pilot: migrate a representative workload set; confirm CPU ready < 5 % and latency targets.")
    rec.append("90-day post-migration review of utilization against this sizing.")
    return rec


def size_project(project: Project, policy: Policy, cache: BenchCache | None = None,
                 prices: dict[str, Any] | None = None) -> SizingRun:
    cache = cache or ensure_db()
    prices = prices or load_prices()
    clusters = []
    for c in project.clusters:
        if not c.hosts and not c.workloads:
            continue
        clusters += size_cluster(c, policy, cache, prices)
    run = SizingRun(generated_at=datetime.now().isoformat(timespec="seconds"),
                    policy=policy.model_dump(mode="json"), clusters=clusters,
                    benchmark_db={"path": str(cache.path), "results": cache.count(),
                                  "curated": cache.count("curated"), "live": cache.count("live"),
                                  "last_update_cpu2017": cache.get_meta("last_update_cpu2017")},
                    disclaimers=DISCLAIMERS)
    if policy.vmware_order_min_cores == 0:
        run.warnings.append(VMWARE_ORDER_WARNING)
    project.sizing = run.model_dump(mode="json")
    return run
