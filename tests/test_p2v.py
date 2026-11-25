"""P2V: pCore:vCore ratio for physical servers sized from inventory only (global, cluster, server, pattern)."""

from __future__ import annotations

from pathlib import Path

import pytest

from refreshsizer.config.schema import load_policy
from refreshsizer.engine.demand import p2v_ratio_for
from refreshsizer.engine.sizing import size_project
from refreshsizer.ingest.pipeline import ingest_files
from refreshsizer.model.entities import SourceHost

INV = "model_only_inventory.xlsx"  # 6 physical servers, no VMs, no utilization


def _d0(fixtures_dir: Path, ov: dict | None = None):  # type: ignore[no-untyped-def]
    p = ingest_files([fixtures_dir / INV], llm=False)
    return p, size_project(p, load_policy(ov)).clusters[0].demand


def test_global_ratio_divides_capacity(fixtures_dir: Path) -> None:
    _, d1 = _d0(fixtures_dir)
    _, d3 = _d0(fixtures_dir, {"p2v_ratio": 3})
    assert d1.d0 == pytest.approx(d1.su_cap_total, rel=1e-3)  # 1:1 = full capacity (unchanged default)
    assert d3.d0 == pytest.approx(d1.su_cap_total / 3, rel=1e-3)
    assert d3.k_virt == pytest.approx(1.07)  # physical -> virtual overhead
    assert any(f.startswith("P2V: 6 physical server(s)") for f in d3.flags)


def test_precedence_server_pattern_cluster_global() -> None:
    pol = load_policy({"p2v_ratio": 2, "p2v_overrides": {"cluster:Prod*": 3, "host:sql*": 1.5, "host:sql01": 1}})
    assert p2v_ratio_for(SourceHost(host_id="sql01"), "Prod-A", pol) == (1.0, "host:sql01")  # exact host
    assert p2v_ratio_for(SourceHost(host_id="sql02"), "Prod-A", pol) == (1.5, "host:sql*")  # host pattern
    assert p2v_ratio_for(SourceHost(host_id="web01"), "Prod-A", pol) == (3.0, "cluster:Prod*")
    assert p2v_ratio_for(SourceHost(host_id="web01"), "Dev", pol) == (2.0, "global p2v_ratio")
    assert p2v_ratio_for(SourceHost(host_id="web01", p2v_ratio=5), "Prod", pol) == (5.0, "input file")
    with pytest.raises(ValueError, match="p2v"):
        load_policy({"p2v_ratio": 0})
    with pytest.raises(ValueError, match="p2v_overrides"):
        load_policy({"p2v_overrides": {"rack:1": 2}})


def test_one_server_override_and_measured_data_wins(fixtures_dir: Path) -> None:
    p, d_all3 = _d0(fixtures_dir, {"p2v_ratio": 3})
    first = p.clusters[0].hosts[0]
    _, d_mix = _d0(fixtures_dir, {"p2v_ratio": 3, "p2v_overrides": {f"host:{first.host_id}": 1}})
    cap = next(h.su_cap for h in d_all3.hosts if h.host_id == first.host_id)
    assert d_mix.d0 - d_all3.d0 == pytest.approx(cap - cap / 3, rel=1e-3)
    # clusters with measured utilization ignore the ratio
    m1 = size_project(ingest_files([fixtures_dir / "liveoptics_estate.xlsx"]), load_policy()).clusters[0].demand
    m3 = size_project(ingest_files([fixtures_dir / "liveoptics_estate.xlsx"]),
                      load_policy({"p2v_ratio": 3})).clusters[0].demand
    assert m1.d0 == m3.d0


def test_manual_template_column(tmp_path: Path) -> None:
    import openpyxl

    from refreshsizer.ingest.manual_template import generate_template, parse

    f = generate_template(tmp_path / "t.xlsx")
    wb = openpyxl.load_workbook(f)
    ws = wb["Hosts"]
    header = [c.value for c in ws[1]] if ws[1][0].value == "host_id" else None
    row_hdr = 1
    if header is None:  # header row further down (title rows)
        for r in range(1, 10):
            if ws.cell(r, 1).value == "host_id":
                row_hdr, header = r, [c.value for c in ws[r]]
                break
    assert header and "p2v_ratio" in header
    col = header.index("p2v_ratio") + 1
    vals = {"host_id": "srv01", "cpu_model": "Intel Xeon E5-2680 v4", "sockets": 2, "cores_per_socket": 14,
            "hypervisor": "bare-metal", "p2v_ratio": 4}
    for k, v in vals.items():
        ws.cell(row_hdr + 1, header.index(k) + 1, v)
    for r in range(row_hdr + 2, ws.max_row + 1):  # drop example rows
        for c in range(1, ws.max_column + 1):
            ws.cell(r, c, None)
    wb.save(f)
    res = parse(f)
    h = next(h for h in res.hosts if h.host_id == "srv01")
    assert h.p2v_ratio == 4 and col > 0
