"""Golden tests A, B, C (tolerance 0.5 %)."""

from __future__ import annotations

from pathlib import Path

import pytest

from refreshsizer.benchmarks.curated_db import ensure_db
from refreshsizer.config.schema import load_policy, load_prices
from refreshsizer.engine.naive import naive
from refreshsizer.engine.optimizer import evaluate_named
from refreshsizer.engine.sizing import size_project
from refreshsizer.ingest import rvtools
from refreshsizer.ingest.base import assemble
from refreshsizer.ingest.pipeline import ingest_files
from refreshsizer.model.entities import SizingMode

TOL = 0.005


def approx(v: float, ref: float) -> bool:
    return abs(v - ref) <= abs(ref) * TOL


def test_golden_a_liveoptics_summary(fixtures_dir: Path) -> None:
    policy = load_policy()
    proj = ingest_files([fixtures_dir / "liveoptics_estate.xlsx"])
    run = size_project(proj, policy)
    cs = run.clusters[0]
    d = cs.demand
    assert d.mode == SizingMode.SUMMARY
    assert approx(d.spc_source, 5.32) and approx(d.su_cap_total, 1490)
    assert approx(d.d0, 819.5)
    assert approx(d.d_t, 1648.4)
    assert approx(d.c_req, 2197.8)
    c = evaluate_named(d, "R7725", "amd-epyc-9355", policy, load_prices(), ensure_db())
    assert approx(c.su_host, 964)
    assert c.n_active == 3 and c.k_ha == 1 and c.n_total == 4
    assert abs(c.util_failed - 0.57) < 0.005
    nv = naive(d, c.config.total_cores, 3.55, policy)
    assert nv.core_match_hosts == 13 and nv.ghz_match_hosts == 6
    assert approx(d.m_t, 6408)
    assert c.dimm and c.dimm.dimm_count == 24 and c.dimm.dimm_size_gb == 96
    assert approx(d.storage_raw_tib, 207.4)
    assert c.drives and c.drives.count == 8 and c.drives.drive_tb == 7.68
    assert cs.recommended is not None and cs.uncertainty is not None
    assert cs.uncertainty.p90_hosts >= cs.uncertainty.p50_hosts


def test_golden_b_power_rperf(fixtures_dir: Path) -> None:
    policy = load_policy({"growth": {"cpu_pct": 10}})
    proj = ingest_files([fixtures_dir / "aix_nmon_summary.csv"])
    run = size_project(proj, policy)
    cs = run.clusters[0]
    d = cs.demand
    rperf = next(e for e in d.explanations if e.step_id == "d0").inputs["rperf_demand"]
    assert approx(rperf, 359.9)
    assert approx(d.d0, 153.0)
    assert d.k_arch == 1.25
    assert approx(d.c_req, 439.9)
    assert d.is_db_cluster and d.external_storage
    c = evaluate_named(d, "R7725", "amd-epyc-9175f", policy, load_prices(), ensure_db())
    assert approx(c.su_host, 579)
    assert c.n_total == 2
    assert c.oracle_licenses_active == 16 and c.oracle_licenses_all == 32
    assert cs.recommended is not None
    assert cs.recommended.config.platform == "R7725" and cs.recommended.config.cpu_id == "amd-epyc-9175f"


def test_golden_c_inventory_only(fixtures_dir: Path) -> None:
    policy = load_policy()
    r = rvtools.parse(fixtures_dir / "rvtools_estate.xlsx")
    for h in r.hosts:
        h.cpu_util_snapshot_pct = None
    for w in r.workloads:
        w.cpu_mhz_snapshot = None
    proj = assemble([r])
    run = size_project(proj, policy)
    cs = run.clusters[0]
    d = cs.demand
    assert d.mode == SizingMode.INVENTORY_ONLY
    assert approx(d.d0, 1490) and approx(d.d0_by_path["inventory_vcpu"], 840 * 149 / 28)
    assert approx(d.d_t, 2997)
    assert approx(d.c_req, 3996)
    c = evaluate_named(d, "R7725", "amd-epyc-9355", policy, load_prices(), ensure_db())
    assert c.n_active == 5 and c.n_total == 6
    nv = naive(d, 64, 3.55, policy)
    assert nv.core_match_hosts == 13
    assert [row["assumed_util"] for row in d.inventory_sensitivity] == [1.0, 0.7, 0.5]
    assert cs.inventory_no_headroom_hosts is not None


@pytest.mark.parametrize("g", [5, 15, 30])
def test_more_growth_more_hosts_smoke(fixtures_dir: Path, g: int) -> None:
    policy = load_policy({"growth": {"cpu_pct": g}})
    proj = ingest_files([fixtures_dir / "liveoptics_estate.xlsx"])
    run = size_project(proj, policy)
    assert run.clusters[0].recommended is not None
