"""Disaggregated (external storage / PowerStore) sizing, storage design section and the customer email prompt."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from refreshsizer.catalog.loader import load_powerstore
from refreshsizer.cli import app
from refreshsizer.config.schema import load_policy
from refreshsizer.engine.results import SizingRun
from refreshsizer.engine.sizing import size_project
from refreshsizer.engine.storage_design import usable_tib
from refreshsizer.ingest.pipeline import ingest_files
from refreshsizer.report.build import write_reports
from refreshsizer.report.email_prompt import build_email_prompt

runner = CliRunner()


def _sized(fixtures_dir: Path, ov: dict | None = None):  # type: ignore[no-untyped-def]
    p = ingest_files([fixtures_dir / "liveoptics_estate.xlsx"])
    run = size_project(p, load_policy(ov))
    return p, run


def test_hci_storage_design(fixtures_dir: Path) -> None:
    _, run = _sized(fixtures_dir)
    cs = run.clusters[0]
    st, rec = cs.storage, cs.recommended
    assert st and rec and rec.drives and st.architecture == "hci" and st.array is None
    assert st.raw_tib == pytest.approx(rec.drives.raw_tib * rec.n_total, rel=1e-3)
    assert st.usable_tib == pytest.approx(st.raw_tib / cs.demand.p_prot, rel=1e-3)
    assert st.fill_t <= 1 - load_policy().slack + 1e-6  # slack respected
    assert "vSAN ESA" in st.solution and st.protection_label.startswith("vSAN ESA RAID-5")


def test_external_powerstore_design(fixtures_dir: Path) -> None:
    _, hci = _sized(fixtures_dir)
    _, run = _sized(fixtures_dir, {"storage_architecture": "external"})
    cs = run.clusters[0]
    st, rec, d = cs.storage, cs.recommended, cs.demand
    assert d.external_storage and d.protection == "external" and rec and rec.drives is None
    assert rec.n_total <= hci.clusters[0].recommended.n_total  # no vSAN minimum hosts / CPU reserve
    assert not any("vSAN" in line.product for line in rec.licenses)
    assert st and st.architecture == "external" and st.array is not None
    a = st.array
    assert a.model.startswith("PowerStore") and a.usable_tib * 0.80 >= st.physical_t_tib - 1e-6
    assert a.fill_t <= 0.80 + 1e-6 and a.fill_today < a.fill_t
    assert a.drives_per_appliance >= 6 and a.capex_usd > 0 and st.capex_usd > a.capex_usd  # + host adapters
    assert st.host_adapter == "1 x dual-port 32 Gb FC HBA" and st.fabrics == 2
    assert st.switch_ports_per_fabric == rec.n_total + st.array_ports // 2
    assert st.alternatives and all(o.capex_usd >= a.capex_usd for o in st.alternatives)  # cheapest first
    assert "PowerStore" in cs.target_selection
    # identical CPU demand: storage architecture does not change D0 / D_T
    assert d.d_t == hci.clusters[0].demand.d_t


def test_external_options(fixtures_dir: Path) -> None:
    _, run = _sized(fixtures_dir, {"storage_architecture": "external", "external_storage": {
        "drr": 3, "parity": "double", "protocol": "nvme_tcp25", "models": ["PowerStore 3200T"]}})
    st = run.clusters[0].storage
    assert st and st.array and st.array.model == "PowerStore 3200T" and not st.alternatives
    assert st.drr == 3 and st.physical_t_tib == pytest.approx(st.used_t_tib / 3, rel=1e-3)
    assert "25 GbE NIC" in st.host_adapter and st.protocol_label.startswith("NVMe/TCP")
    _, run2 = _sized(fixtures_dir, {"storage_architecture": "external", "external_storage": {"array": "existing"}})
    st2 = run2.clusters[0].storage
    assert st2 and st2.array is None and "existing" in st2.solution and st2.capex_usd > 0


def test_dre_usable_grows_with_drives() -> None:
    cat = load_powerstore()
    vals = [usable_tib(n, 7.68, "single", cat) for n in range(6, 40)]
    assert all(b >= a for a, b in zip(vals, vals[1:], strict=False))
    assert usable_tib(5, 7.68, "single", cat) == 0  # below the DRE minimum
    assert usable_tib(20, 7.68, "double", cat) < usable_tib(20, 7.68, "single", cat) + 1e-9


def test_policy_validation() -> None:
    with pytest.raises(ValueError, match="Nutanix targets are HCI only"):
        load_policy({"target_stack": "nutanix_ahv", "storage_architecture": "external"})
    with pytest.raises(ValueError, match="external_storage.protocol"):
        load_policy({"external_storage": {"protocol": "smoke-signals"}})
    with pytest.raises(ValueError, match="unknown"):
        load_policy({"external_storage": {"models": ["PowerStore 1T"]}})
    assert load_policy({"target_stack": None}).target_stack == "vmware_vsan"  # None = back to default


@pytest.mark.parametrize("level", ["customer", "presales", "full"])
def test_storage_section_in_reports(fixtures_dir: Path, tmp_path: Path, level: str) -> None:
    import docx

    p, _ = _sized(fixtures_dir, {"storage_architecture": "external"})
    out = write_reports(p, tmp_path, ["html", "docx", "xlsx"], level=level)
    html = next(x for x in out if x.suffix == ".html").read_text(encoding="utf-8")
    assert "Storage design" in html and "PowerStore" in html and "Fibre Channel" in html
    text = "\n".join(c.text for t in docx.Document(str(next(x for x in out if x.suffix == ".docx"))).tables
                     for r in t.rows for c in r.cells)
    assert "PowerStore" in text and "Usable capacity proposed" in text
    if level == "customer":
        assert "$" not in html and "Other array models" not in html
    else:
        assert "Storage total" in html and "Other array models" in html
    if level == "full":
        assert "External array capacity requirement" in html


def test_email_prompt(fixtures_dir: Path) -> None:
    p, run = _sized(fixtures_dir, {"storage_architecture": "external"})
    rec = run.clusters[0].recommended
    assert rec
    txt = build_email_prompt(p, recipient="J. Doe", sender="Alex", notes="exit old support in Q3")
    assert "Use ONLY the facts" in txt and f"{rec.n_total} x Dell PowerEdge {rec.config.platform}" in txt
    assert "PowerStore" in txt and "J. Doe" in txt and "exit old support in Q3" in txt
    assert "$" not in txt and "Do not mention any price" in txt
    priced = build_email_prompt(p, include_prices=True, audience="technical")
    assert "$" in priced and "budgetary" in priced and "infrastructure team" in priced
    fr = build_email_prompt(p, lang="fr")
    assert "Rédigez l'e-mail en français" in fr and "FAITS" in fr


def test_cli_storage_and_prompt(fixtures_dir: Path, tmp_path: Path) -> None:
    pj = tmp_path / "p.json"
    assert runner.invoke(app, ["ingest", str(fixtures_dir / "liveoptics_estate.xlsx"), "--yes", "--out",
                               str(pj)]).exit_code == 0
    r = runner.invoke(app, ["size", str(pj), "--storage", "external", "--protocol", "iscsi25", "--array-drr", "2.5"])
    assert r.exit_code == 0, r.output
    import json

    st = json.loads(pj.read_text())["sizing"]["clusters"][0]["storage"]
    assert st["architecture"] == "external" and st["protocol"] == "iscsi25" and st["drr"] == 2.5
    r = runner.invoke(app, ["email-prompt", str(pj), "--lang", "fr", "--to", "Mme Martin", "--out",
                            str(tmp_path / "mail.txt")])
    assert r.exit_code == 0, r.output
    assert "Mme Martin" in (tmp_path / "mail.txt").read_text(encoding="utf-8")
    r = runner.invoke(app, ["report", str(pj), "--format", "prompt", "--out", str(tmp_path / "rep")])
    assert r.exit_code == 0 and (tmp_path / "rep" / "refreshsizer_email_prompt.txt").exists()
    SizingRun.model_validate(json.loads(pj.read_text())["sizing"])  # round-trips
