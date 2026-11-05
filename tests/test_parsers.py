from __future__ import annotations

from pathlib import Path

import pandas as pd

from refreshsizer.ingest import awr, liveoptics, manual_template, nmon, rvtools
from refreshsizer.ingest.common import find_col, pct_value, to_float
from refreshsizer.ingest.detect import detect_format
from refreshsizer.ingest.pipeline import ingest_files


def test_detect(fixtures_dir: Path) -> None:
    assert detect_format(fixtures_dir / "rvtools_estate.xlsx") == "rvtools"
    assert detect_format(fixtures_dir / "liveoptics_estate.xlsx") == "liveoptics"
    assert detect_format(fixtures_dir / "manual_template_en.xlsx") == "manual_template"
    assert detect_format(fixtures_dir / "manual_template_fr.xlsx") == "manual_template"
    assert detect_format(fixtures_dir / "aix_nmon_summary.csv") == "nmon"
    assert detect_format(fixtures_dir / "oracle_awr_summary.csv") == "awr"
    assert detect_format(fixtures_dir / "liveoptics_timeseries.csv") == "timeseries"
    assert detect_format(fixtures_dir / "messy_customer_fr.xlsx") == "universal"


def test_rvtools(fixtures_dir: Path) -> None:
    r = rvtools.parse(fixtures_dir / "rvtools_estate.xlsx")
    assert len(r.hosts) == 10
    assert len(r.workloads) == 120  # templates + powered-off excluded
    assert sum(w.vcpu or 0 for w in r.workloads) == 840
    assert abs(sum(w.ram_gb_configured or 0 for w in r.workloads) - 3600) < 1e-6
    assert abs(sum(w.storage_used_gib or 0 for w in r.workloads) / 1024 - 70) < 1e-6
    h = r.hosts[0]
    assert h.cores == 28 and abs((h.nominal_ghz or 0) - 2.4) < 1e-9 and h.ram_gb == 512
    assert h.cpu_util_snapshot_pct is not None


def test_liveoptics(fixtures_dir: Path) -> None:
    r = liveoptics.parse(fixtures_dir / "liveoptics_estate.xlsx")
    assert len(r.hosts) == 10 and len(r.workloads) == 120
    assert sum(h.cpu_util_peak_pct or 0 for h in r.hosts) == 550
    assert r.hosts[0].ram_gb == 512


def test_manual(fixtures_dir: Path) -> None:
    for lang in ("en", "fr"):
        r = manual_template.parse(fixtures_dir / f"manual_template_{lang}.xlsx")
        assert len(r.hosts) == 4 and len(r.workloads) == 40, lang
        assert not r.errors
        assert r.hosts[0].cpu_util_p95_pct == 48
        assert r.policy.get("horizon_years") == 5


def test_template_generation(tmp_path: Path) -> None:
    for lang in ("en", "fr"):
        p = manual_template.generate_template(tmp_path / f"t_{lang}.xlsx", lang)
        assert detect_format(p) == "manual_template"
        r = manual_template.parse(p)
        assert r.hosts == [] and not r.errors


def test_nmon_awr(fixtures_dir: Path) -> None:
    r = nmon.parse(fixtures_dir / "aix_nmon_summary.csv")
    assert len(r.hosts) == 2 and len(r.workloads) == 4
    assert sum(w.physc_p95 or 0 for w in r.workloads) == 18
    a = awr.parse(fixtures_dir / "oracle_awr_summary.csv")
    assert len(a.workloads) == 2 and a.workloads[0].db_cpu_p95_cores == 9.5


def test_column_tolerance() -> None:
    cols = ["  cpu MODEL ", "Memory (MiB)", "# Cores"]
    assert find_col(cols, "CPU Model") == "  cpu MODEL "
    assert find_col(cols, "Memory") == "Memory (MiB)"
    assert find_col(cols, "# cores") == "# Cores"
    assert to_float("2,4") == 2.4 and to_float("1 234,5") == 1234.5 and to_float("1,234.5") == 1234.5
    assert pct_value("0,55") == 55.0 and pct_value("55 %") == 55.0


def test_rvtools_column_order_and_case(tmp_path: Path, fixtures_dir: Path) -> None:
    sheets = pd.read_excel(fixtures_dir / "rvtools_estate.xlsx", sheet_name=None)
    with pd.ExcelWriter(tmp_path / "rv.xlsx") as xw:
        for name, df in sheets.items():
            df = df[list(reversed(df.columns))]
            df.columns = [c.upper() for c in df.columns]
            df.to_excel(xw, sheet_name=name, index=False)
    r = rvtools.parse(tmp_path / "rv.xlsx")
    assert len(r.hosts) == 10 and sum(w.vcpu or 0 for w in r.workloads) == 840


def test_pipeline_project(fixtures_dir: Path) -> None:
    p = ingest_files([fixtures_dir / "liveoptics_estate.xlsx"])
    assert len(p.clusters) == 1
    assert p.data_quality[0].mode.value == "SUMMARY"
    p2 = ingest_files([fixtures_dir / "rvtools_estate.xlsx"])
    assert p2.data_quality[0].mode.value == "SNAPSHOT"
    p3 = ingest_files([fixtures_dir / "liveoptics_estate.xlsx", fixtures_dir / "liveoptics_timeseries.csv"])
    assert p3.data_quality[0].mode.value == "MEASURED"
