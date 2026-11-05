from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from refreshsizer.benchmarks.curated_db import ensure_db
from refreshsizer.catalog.dimm_rules import choose_dimms, is_balanced
from refreshsizer.catalog.loader import load_platforms
from refreshsizer.config.schema import load_policy, load_prices
from refreshsizer.engine import ha
from refreshsizer.engine.demand import percentile_nearest_rank
from refreshsizer.engine.licensing import target_licensing
from refreshsizer.engine.optimizer import evaluate_named, pareto, sort_key
from refreshsizer.engine.results import ClusterDemand
from refreshsizer.engine.sizing import size_project, split_cluster
from refreshsizer.engine.uncertainty import run as mc_run
from refreshsizer.ingest.pipeline import ingest_files
from refreshsizer.model.entities import Confidence, SizingMode


@pytest.fixture(scope="module")
def lo_run(fixtures_dir: Path):  # type: ignore[no-untyped-def]
    return size_project(ingest_files([fixtures_dir / "liveoptics_estate.xlsx"]), load_policy())


def _demand(**kw: object) -> ClusterDemand:
    base = dict(cluster="t", source_cluster="t", mode=SizingMode.SUMMARY, confidence=Confidence.MEDIUM,
                d0=800, d0_adj=800, d_t=1600, c_req=2133.3, u_max=0.75, growth_cpu_pct=15, m_t=4000,
                storage_raw_tib=100, spc_source=5.32, spc_source_min=5.1, spc_source_max=5.5, source_cores=280,
                total_vcpu=840, largest_vm_vcpu=8)
    base.update(kw)
    return ClusterDemand(**base)  # type: ignore[arg-type]


def test_percentile_nearest_rank() -> None:
    assert percentile_nearest_rank(list(range(1, 101)), 95) == 95
    assert percentile_nearest_rank([5, 1, 3], 95) == 5
    assert percentile_nearest_rank([], 95) == 0


def test_top5_and_pareto(lo_run) -> None:  # type: ignore[no-untyped-def]
    cs = lo_run.clusters[0]
    assert 1 <= len(cs.candidates) <= 5 and cs.evaluated > 50
    keys = [sort_key(c, "min_tco", False) for c in cs.candidates]
    assert keys == sorted(keys)
    front = cs.pareto
    for a in front:
        assert not any(b.n_total <= a.n_total and b.tco < a.tco for b in front if b is not a)
    assert pareto(cs.candidates)  # non-empty


@pytest.mark.parametrize("objective", ["min_hosts", "min_power", "min_licensed_cores", "max_headroom"])
def test_objectives(fixtures_dir: Path, objective: str) -> None:
    run = size_project(ingest_files([fixtures_dir / "liveoptics_estate.xlsx"]), load_policy({"objective": objective}))
    cs = run.clusters[0]
    assert cs.recommended is not None
    if objective == "min_hosts":
        assert cs.recommended.n_total == min(c.n_total for c in cs.candidates)


def test_ha_rules() -> None:
    pol = load_policy()
    p = ha.plan(1000, 1333.3, 100, pol, "vsan_esa_raid5_4p1")
    assert p.n_active == 14 and p.k_ha == 1 and p.n_total == 15
    p = ha.plan(1600, 2133.3, 100, pol, "vsan_esa_raid5_4p1")
    assert p.k_ha == 2  # > 16 hosts -> N+2
    p = ha.plan(10, 13.3, 100, pol, "vsan_esa_raid5_4p1")
    assert p.n_total == 4  # vSAN RAID-5 4+1 minimum
    p = ha.plan(10, 13.3, 100, pol, "external")
    assert p.n_total == 2
    pol3 = load_policy({"max_hosts_per_cluster_user": 3})
    from refreshsizer.engine.storage import resolve_protection

    assert resolve_protection(pol3, False)[0] == "vsan_esa_raid5_2p1"


def test_licensing_minimums() -> None:
    pol = load_policy()
    prices = load_prices()
    d = _demand(licensing={"windows_dc": {"workloads": 10, "vcpu": 40, "physc": 0, "vm_cores_min4": 40},
                           "sql_ent": {"workloads": 2, "vcpu": 4, "physc": 0, "vm_cores_min4": 8},
                           "oracle_ee": {"workloads": 1, "vcpu": 8, "physc": 0, "vm_cores_min4": 8}})
    lines, info = target_licensing(d, 3, 1, 2, 8, "AMD EPYC 9015", 5, pol, prices, 50, True)
    by = {line.product: line for line in lines}
    assert by["VMware VCF"].quantity == 3 * 2 * 16  # min 16 cores per CPU
    assert by["Windows Server Datacenter"].quantity == 3 * 16  # min 8/CPU, 16/server
    assert by["SQL Server Enterprise"].quantity == 8  # per-VM (2 VMs x min 4) cheaper than 3 hosts x 2 x 8
    assert info["oracle_all"] == 3 * 16 * 0.5
    pol2 = load_policy({"vmware_order_min_cores": 72})
    lines2, _ = target_licensing(d, 1, 1, 2, 8, "AMD EPYC 9015", 5, pol2, prices, 10, True)
    assert lines2[0].quantity == 72


def test_split_clusters(fixtures_dir: Path) -> None:
    p = ingest_files([fixtures_dir / "manual_template_en.xlsx"])
    parts = split_cluster(p.clusters[0], load_policy())
    assert [x[0] for x in parts] == ["Manual-Cluster-db", "Manual-Cluster"] and parts[0][3]
    assert abs(parts[0][2] - 32 / 176) < 1e-9
    off = split_cluster(p.clusters[0], load_policy({"split_clusters": False}))
    assert len(off) == 1


def test_monte_carlo_deterministic(lo_run) -> None:  # type: ignore[no-untyped-def]
    cs = lo_run.clusters[0]
    u1 = mc_run(cs.demand, cs.recommended, load_policy())
    u2 = mc_run(cs.demand, cs.recommended, load_policy())
    assert u1.p50_hosts == u2.p50_hosts and u1.histogram == u2.histogram and u1.runs == 5000
    assert u1.p90_hosts >= u1.p50_hosts and u1.tornado


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(g1=st.floats(min_value=0, max_value=40), dg=st.floats(min_value=0, max_value=20))
def test_more_growth_never_fewer_hosts(g1: float, dg: float) -> None:
    pol = load_policy()
    prices, cache = load_prices(), ensure_db()

    def hosts(g: float) -> int:
        f = (1 + g / 100) ** 5
        d = _demand(d_t=800 * f, c_req=800 * f / 0.75, m_t=4000 * (1.1 ** 5), growth_cpu_pct=g)
        return evaluate_named(d, "R7725", "amd-epyc-9355", pol, prices, cache).n_total

    assert hosts(g1 + dg) >= hosts(g1)


@given(need=st.floats(min_value=1, max_value=8000), plat=st.sampled_from(sorted(load_platforms())),
       cpus=st.integers(min_value=1, max_value=2))
def test_dimm_always_balanced(need: float, plat: str, cpus: int) -> None:
    p = load_platforms()[plat]
    cpus = min(cpus, p.sockets)
    d = choose_dimms(p, cpus, 32, "P", 6400, need)
    if d is not None:
        assert is_balanced(d, p, cpus)
        assert d.total_gb >= need and d.dimm_count == p.channels_per_socket * cpus * d.dpc
