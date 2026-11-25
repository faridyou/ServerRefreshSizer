"""Several source clusters -> one target cluster (+ one shared array); SPEC Score naming."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from refreshsizer.cli import app
from refreshsizer.config.schema import load_policy
from refreshsizer.engine.sizing import size_project
from refreshsizer.ingest.pipeline import ingest_files
from refreshsizer.report.build import write_reports

runner = CliRunner()
MULTI = "messy_customer_fr.xlsx"  # Cluster-Paris + Cluster-Lyon


def _run(fixtures_dir: Path, files: list[str], ov: dict | None = None):  # type: ignore[no-untyped-def]
    p = ingest_files([fixtures_dir / f for f in files], llm=False)
    return p, size_project(p, load_policy(ov))


def test_default_is_one_consolidated_cluster(fixtures_dir: Path) -> None:
    _, sep = _run(fixtures_dir, [MULTI], {"consolidation": "per_cluster"})
    _, one = _run(fixtures_dir, [MULTI])
    assert [c.demand.cluster for c in sep.clusters] == ["Cluster-Paris", "Cluster-Lyon"]
    assert len(one.clusters) == 1
    d = one.clusters[0].demand
    assert d.cluster == "Consolidated" and d.source_clusters == ["Cluster-Paris", "Cluster-Lyon"]
    for attr in ("d0", "d0_adj", "d_t", "c_req", "m_t", "su_cap_total", "storage_used_t_tib", "storage_raw_tib"):
        assert getattr(d, attr) == pytest.approx(sum(getattr(c.demand, attr) for c in sep.clusters), rel=1e-4)
    assert d.source_cores == sum(c.demand.source_cores for c in sep.clusters)
    assert d.u_max == pytest.approx(d.d_t / d.c_req, rel=1e-3)
    # one HA reserve instead of one per cluster
    rec = one.clusters[0].recommended
    assert rec and rec.n_total <= sum(c.recommended.n_total for c in sep.clusters if c.recommended)
    assert any(e.step_id == "consolidation" for e in d.explanations)


def test_single_cluster_unchanged(fixtures_dir: Path) -> None:
    _, a = _run(fixtures_dir, ["liveoptics_estate.xlsx"])
    _, b = _run(fixtures_dir, ["liveoptics_estate.xlsx"], {"consolidation": "per_cluster"})
    da, db = a.clusters[0].demand, b.clusters[0].demand
    assert da.cluster == db.cluster == "Prod-Cluster" and da.c_req == db.c_req
    assert a.clusters[0].recommended.label == b.clusters[0].recommended.label  # type: ignore[union-attr]


def test_db_split_and_one_shared_array(fixtures_dir: Path) -> None:
    files = ["liveoptics_estate.xlsx", "aix_nmon_summary.csv", "oracle_awr_summary.csv"]
    _, run = _run(fixtures_dir, files, {"storage_architecture": "external"})
    names = [c.demand.cluster for c in run.clusters]
    assert names == ["Consolidated-db", "Consolidated"]  # per-core-licensed databases keep their own cluster
    db, main = run.clusters
    assert db.storage is None and any("shared" in w for w in db.warnings)
    st = main.storage
    assert st and st.array and st.hosts == sum(c.recommended.n_total for c in run.clusters if c.recommended)
    total_used = sum(c.demand.storage_used_t_tib for c in run.clusters)
    assert st.used_t_tib == pytest.approx(total_used, rel=1e-3)  # the array holds the whole estate
    _, one = _run(fixtures_dir, files, {"split_clusters": False})
    assert [c.demand.cluster for c in one.clusters] == ["Consolidated"]


def test_growth_events_counted_once(fixtures_dir: Path) -> None:
    ev = {"growth_events": [{"year": 2, "su": 100, "mem_gib": 512, "storage_tib": 20}]}
    _, base = _run(fixtures_dir, [MULTI])
    _, with_ev = _run(fixtures_dir, [MULTI], ev)
    a, b = base.clusters[0].demand, with_ev.clusters[0].demand
    assert b.d_t - a.d_t == pytest.approx(100, rel=1e-3)
    assert b.storage_used_t_tib - a.storage_used_t_tib == pytest.approx(20, rel=1e-3)


def test_reports_and_cli(fixtures_dir: Path, tmp_path: Path) -> None:
    p, _ = _run(fixtures_dir, [MULTI])
    out = write_reports(p, tmp_path, ["html"], level="presales")
    html = out[0].read_text(encoding="utf-8")
    assert "consolidating 2 source clusters: Cluster-Paris, Cluster-Lyon" in html
    assert "SPEC Score" in html and " SU<" not in html and "SPEC Units" not in html
    assert "not a SPEC result" in html  # unit definition
    pj = tmp_path / "p.json"
    runner.invoke(app, ["ingest", str(fixtures_dir / MULTI), "--yes", "--no-llm", "--out", str(pj)])
    r = runner.invoke(app, ["size", str(pj), "--clusters", "per_cluster"])
    assert r.exit_code == 0, r.output
    import json

    assert len(json.loads(pj.read_text())["sizing"]["clusters"]) == 2
    with pytest.raises(ValueError, match="consolidation"):
        load_policy({"consolidation": "some"})


def test_naive_core_and_ghz_match_are_consistent(fixtures_dir: Path) -> None:
    """Regression: core match used INSTALLED cores while GHz match used USED GHz (e.g. 51 vs 6 hosts at ~16 % load).
    Both now start from the same measured load, so they differ only by the clock ratio."""
    import math

    from refreshsizer.catalog.loader import load_cpus
    from refreshsizer.engine.growth import growth_factor
    from refreshsizer.engine.naive import naive, used_share

    for files in ([MULTI], ["liveoptics_estate.xlsx"]):
        _, run = _run(fixtures_dir, files)
        cs = run.clusters[0]
        d, rec = cs.demand, cs.recommended
        assert rec and d.source_ghz_installed > 0
        share, _ = used_share(d)
        assert 0 < share <= 1.0
        cores_host = rec.config.total_cores
        ghz = load_cpus()[rec.config.cpu_id].base_ghz
        f = growth_factor(d.growth_cpu_pct, d.years)
        core_need = d.source_cores * share * f / d.u_max
        ghz_need = d.source_ghz_used * f / d.u_max
        # used GHz = used cores x average source clock -> the two needs differ by the clock ratio only
        avg_src_ghz = d.source_ghz_installed / d.source_cores
        assert ghz_need / ghz == pytest.approx(core_need * avg_src_ghz / ghz, rel=1e-6)
        nv = naive(d, cores_host, ghz, load_policy())
        from refreshsizer.engine.ha import k_ha_for

        n_act = max(1, math.ceil(core_need / cores_host - 1e-9))
        assert nv.core_match_hosts == n_act + k_ha_for(n_act, load_policy())
        assert abs(nv.core_match_hosts - nv.ghz_match_hosts) <= max(2, nv.ghz_match_hosts)  # same order of magnitude
