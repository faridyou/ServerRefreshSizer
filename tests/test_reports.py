from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest
from typer.testing import CliRunner

from refreshsizer.cli import app
from refreshsizer.config.schema import load_policy
from refreshsizer.engine.sizing import size_project
from refreshsizer.ingest.pipeline import ingest_files
from refreshsizer.report.build import translator, write_reports

runner = CliRunner()
FIXTURES = ["liveoptics_estate.xlsx", "rvtools_estate.xlsx", "manual_template_en.xlsx", "manual_template_fr.xlsx",
            "aix_nmon_summary.csv", "oracle_awr_summary.csv", "messy_customer_fr.xlsx", "messy_semicolon.csv",
            "model_only_inventory.xlsx"]


@pytest.mark.parametrize("name", FIXTURES)
def test_run_end_to_end(fixtures_dir: Path, tmp_path: Path, name: str) -> None:
    files = [str(fixtures_dir / name)]
    if name == "liveoptics_estate.xlsx":
        files.append(str(fixtures_dir / "liveoptics_timeseries.csv"))
    res = runner.invoke(app, ["run", *files, "--yes", "--no-llm", "--out", str(tmp_path)])
    assert res.exit_code == 0, res.output
    html = (tmp_path / "refreshsizer_report.html").read_text(encoding="utf-8")
    for i in range(1, 15):
        assert f">{i}. " in html, f"section {i} missing"
    assert "SPEC" in html and "data:image/png;base64," in html
    assert (tmp_path / "refreshsizer_report.xlsx").exists() and (tmp_path / "project.json").exists()
    assert (tmp_path / "refreshsizer_report.pdf").exists()  # PDF is best-effort; it works for all fixtures


def test_excel_live_formulas(fixtures_dir: Path, tmp_path: Path) -> None:
    proj = ingest_files([fixtures_dir / "liveoptics_estate.xlsx"])
    size_project(proj, load_policy())
    (xlsx,) = write_reports(proj, tmp_path, ["xlsx"])
    wb = openpyxl.load_workbook(xlsx)
    assert len(wb.sheetnames) == 14
    ws = wb["6 Growth headroom HA"]
    formulas = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("=")]
    assert any("CEILING" in f for f in formulas) and any("^" in f for f in formulas)
    assert any(str(c.value).startswith("=SUM(") for row in wb["5 Normalization"].iter_rows() for c in row)
    assert any(str(c.value).startswith("=") for row in wb["1 Summary"].iter_rows() for c in row)


def test_french_report(fixtures_dir: Path, tmp_path: Path) -> None:
    proj = ingest_files([fixtures_dir / "rvtools_estate.xlsx"])
    size_project(proj, load_policy())
    (html,) = write_reports(proj, tmp_path, ["html"], lang="fr")
    txt = html.read_text(encoding="utf-8")
    assert "Synthèse" in txt and "Questions pour le client" in txt and "CONFIANCE FAIBLE" in txt
    assert translator("fr")("s12") == "12. Incertitude"


def test_pdf_failure_does_not_fail(fixtures_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import xhtml2pdf.pisa as pisa

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("engine broken")

    monkeypatch.setattr(pisa, "CreatePDF", boom)
    proj = ingest_files([fixtures_dir / "aix_nmon_summary.csv"])
    size_project(proj, load_policy())
    out = write_reports(proj, tmp_path, ["pdf"])
    assert [p.suffix for p in out] == [".html"]


def test_cli_template_size_report(tmp_path: Path, fixtures_dir: Path) -> None:
    assert runner.invoke(app, ["template", "--out", str(tmp_path / "t.xlsx"), "--lang", "fr"]).exit_code == 0
    pj = tmp_path / "p.json"
    r = runner.invoke(app, ["ingest", str(fixtures_dir / "aix_nmon_summary.csv"), "--yes", "--out", str(pj)])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["size", str(pj), "--growth", "10", "--years", "5", "--objective", "min_hosts"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["report", str(pj), "--format", "html,xlsx", "--out", str(tmp_path / "r")])
    assert r.exit_code == 0, r.output
    assert runner.invoke(app, ["bench", "show", "EPYC 9355"]).exit_code == 0
    assert runner.invoke(app, ["bench", "update", "--offline"]).exit_code == 0


def test_cli_target_flags(tmp_path: Path, fixtures_dir: Path) -> None:
    import json

    pj = tmp_path / "p.json"
    assert runner.invoke(app, ["ingest", str(fixtures_dir / "liveoptics_estate.xlsx"), "--yes", "--out",
                               str(pj)]).exit_code == 0
    r = runner.invoke(app, ["size", str(pj), "--cpu-vendor", "intel", "--platforms", "R670,R770"])
    assert r.exit_code == 0, r.output
    sz = json.loads(pj.read_text())["sizing"]["clusters"][0]
    assert sz["recommended"]["config"]["platform"] in ("R670", "R770") and "CPU scenarios" in r.output
    r = runner.invoke(app, ["size", str(pj), "--cpus", "EPYC 9355"])
    assert json.loads(pj.read_text())["sizing"]["clusters"][0]["recommended"]["config"]["cpu_id"] == "amd-epyc-9355"
