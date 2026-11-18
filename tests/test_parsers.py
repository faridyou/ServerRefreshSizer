from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

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


def _real_lo_workbook(path: Path) -> Path:
    """Synthetic workbook with the column names of a real Live Optics VMware export (Nov 2026):
    'Host Name' on ESX Hosts but 'Host' on ESX Performance, 'Power State' / 'VM OS' on VMs, Summary sheet."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Summary"
    for k, v in [("Server Count", 2), ("Guest VM Count", 3), ("Start Time", "2026-11-11T15:53:00.000+01:00"),
                 ("End Time", "2026-11-12T15:52:57.963+01:00"), ("Duration", "0.23:59:57.963")]:
        ws.append([k, v])
    h = wb.create_sheet("ESX Hosts")
    h.append(["Host Name", "Datacenter", "vCenter", "Cluster", "OS", "Manufacturer", "Model", "Serial No",
              "CPU Sockets", "CPU Cores", "CPU Description", "Net Clock Speed (GHz)", "CPU Clock Speed (GHz)",
              "CPU Threads", "Memory (KiB)", "Guest VM Count"])
    for i in (1, 2):
        h.append([f"esx{i}.lab.local", "DC", "vc", "clu-a", "VMware ESXi 7.0", "Cisco Systems Inc", "UCSB-B200-M4",
                  f"S{i}", 2, 40, "Intel(R) Xeon(R) CPU E5-2698 v4 @ 2.20GHz 2,19 GHz", 87.6, 2.19, 80,
                  268157856, 2])
    p = wb.create_sheet("ESX Performance")
    p.append(["Host", "Datacenter", "vCenter", "Cluster", "Peak CPU %", "Peak CPU (GHz)", "Average CPU %",
              "Average CPU (GHz)", "Peak Memory %", "Peak Memory (MiB)", "Peak IOPS", "Average IOPS",
              "Peak Throughput MB/s", "Avg Throughput MB/s"])
    p.append(["esx1.lab.local", "DC", "vc", "clu-a", 21, 18.4, 9, 7.9, 40, 100000, 1851, 38, 78.3, 1.0])
    p.append(["esx2.lab.local", "DC", "vc", "clu-a", 16, 14.0, 5, 4.4, 30, 80000, 900, 20, 40.0, 0.5])
    v = wb.create_sheet("VMs")
    v.append(["MOB ID", "VM Name", "IsRunning", "Power State", "VM OS", "Virtual CPU", "Provisioned Memory (MiB)",
              "Guest VM Disk Used (MiB)", "Virtual Disk Size (MiB)", "Template", "Cluster", "Host"])
    v.append(["vm-1", "app1", True, "poweredOn", "Windows Server 2019", 4, 16384, 51200, 102400, False, "clu-a",
              "esx1.lab.local"])
    v.append(["vm-2", "db1", True, "poweredOn", "RHEL 8", 8, 65536, 204800, 409600, False, "clu-a",
              "esx2.lab.local"])
    v.append(["vm-3", "old", False, "poweredOff", "Windows 2008", 2, 4096, 1024, 40960, False, "clu-a",
              "esx2.lab.local"])
    out = path / "LiveOptics_real_headers.xlsx"
    wb.save(out)
    return out


def test_liveoptics_real_export_headers(tmp_path: Path) -> None:
    """Regression: real exports name the host column 'Host' on ESX Performance -> utilization was dropped
    and every cluster was sized INVENTORY_ONLY."""
    f = _real_lo_workbook(tmp_path)
    assert detect_format(f) == "liveoptics"
    r = liveoptics.parse(f)
    assert [h.cpu_util_peak_pct for h in r.hosts] == [21, 16]
    assert len(r.workloads) == 2  # powered-off VM excluded via 'Power State'/'IsRunning'
    assert r.collection_days is not None and abs(r.collection_days - 1.0) < 0.01
    assert any("only 1.0 day" in w for w in r.warnings)
    p = ingest_files([f])
    assert p.data_quality[0].mode.value == "SUMMARY"
    assert p.customer_questions[0].startswith("[clu-a] The Live Optics collection lasted only 1.0 day")


def test_liveoptics_missing_host_column_warns(tmp_path: Path) -> None:
    f = _real_lo_workbook(tmp_path)
    sheets = pd.read_excel(f, sheet_name=None)
    sheets["ESX Performance"] = sheets["ESX Performance"].rename(columns={"Host": "Machine"})
    g = tmp_path / "broken.xlsx"
    with pd.ExcelWriter(g) as xw:
        for k, df in sheets.items():
            df.to_excel(xw, sheet_name=k, index=False, header=k != "Summary")
    r = liveoptics.parse(g)
    assert any("no host column found" in w for w in r.warnings)


def _write_raw_xlsx(path: Path, sheets: dict[str, list[list[object]]]) -> Path:
    """Minimal hand-written SpreadsheetML (x: prefixes, inline strings, no styles) like Live Optics AIR."""
    import zipfile
    from xml.sax.saxutils import escape

    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(path, "w") as z:
        names = list(sheets)
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/>'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships'
                   '+xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
                   'officedocument.spreadsheetml.sheet.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/'
                   'officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        sh = "".join(f'<x:sheet name="{n}" sheetId="{i}" r:id="s{i}" xmlns:r="{rns}"/>' for i, n in
                     enumerate(names, 1))
        z.writestr("xl/workbook.xml", f'<?xml version="1.0"?><x:workbook xmlns:x="{ns}"><x:sheets>{sh}'
                   '</x:sheets></x:workbook>')
        rel = "".join(f'<Relationship Id="s{i}" Type="{rns}/worksheet" Target="worksheets/sheet{i}.xml"/>'
                      for i in range(1, len(names) + 1))
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.'
                   f'openxmlformats.org/package/2006/relationships">{rel}</Relationships>')
        z.writestr("xl/styles.xml", f'<?xml version="1.0"?><x:styleSheet xmlns:x="{ns}"><x:fills><x:fill>'
                   '<x:bogus/></x:fill></x:fills></x:styleSheet>')
        for i, n in enumerate(names, 1):
            rows = "".join("<x:row>" + "".join(
                f'<x:c t="str"><x:v>{escape(str(v))}</x:v></x:c>' if isinstance(v, str) else
                f"<x:c><x:v>{v}</x:v></x:c>" for v in row) + "</x:row>" for row in sheets[n])
            z.writestr(f"xl/worksheets/sheet{i}.xml", f'<?xml version="1.0"?><x:worksheet xmlns:x="{ns}">'
                       f"<x:sheetData>{rows}</x:sheetData></x:worksheet>")
    return path


def test_raw_xlsx_fallback_and_air(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from refreshsizer.ingest import xlsx_raw
    from refreshsizer.ingest.common import read_workbook, sheet_names

    f = _write_raw_xlsx(tmp_path / "LiveOptics_AIR.xlsx", {
        "Details": [["Project ID", 3530729]],
        "Servers": [["Server Name(s)", "OS", "Rule Name", "Rule Details"],
                    ["srv01", "Windows Server 2008", "Microsoft-Server-2008-End-of-life", "Extended support ended"],
                    ["esx01", "VMware ESXi 6.5", "VMware-6.5-End-of-life", "General support ended 2022"]],
        "VMs": [["Guest VM Name", "Guest VM OS", "Rule Name", "Rule Details"],
                ["vm1", "Windows Server 2008 R2", "Microsoft-Server-2008-End-of-life", ""]]})
    assert sheet_names(f) == ["Details", "Servers", "VMs"]
    grids = xlsx_raw.read_xlsx_grids(f)
    assert grids["Details"] == [["Project ID", 3530729]]
    assert list(read_workbook(f)["Servers"].columns)[:2] == ["Server Name(s)", "OS"]
    assert detect_format(f) == "liveoptics_air"
    r = liveoptics.parse_air(f)
    assert len([w for w in r.warnings if w.startswith("Live Optics AIR")]) == 3
    assert not r.hosts and not r.errors
    p = ingest_files([f, _real_lo_workbook(tmp_path)])
    assert p.ingest.formats["LiveOptics_AIR.xlsx"] == "liveoptics_air"
    assert p.data_quality[0].mode.value == "SUMMARY"


def test_ingest_when_openpyxl_cannot_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Files whose stylesheet openpyxl rejects are read by the raw XML fallback, end to end."""
    import openpyxl

    f = _real_lo_workbook(tmp_path)
    air = _write_raw_xlsx(tmp_path / "air.xlsx", {"Details": [["Project ID", 1]],
                                                  "Servers": [["Server Name(s)", "Rule Name", "Rule Details"],
                                                              ["esx1", "VMware-6.5-End-of-life", "ended"]]})

    def refuse(*a: object, **k: object) -> None:
        raise TypeError("expected <class 'openpyxl.styles.fills.Fill'>")

    monkeypatch.setattr(openpyxl, "load_workbook", refuse)
    monkeypatch.setattr(pd, "read_excel", refuse)
    assert detect_format(f) == "liveoptics" and detect_format(air) == "liveoptics_air"
    p = ingest_files([f, air])
    assert p.data_quality[0].mode.value == "SUMMARY"
    assert [h.cpu_util_peak_pct for h in p.clusters[0].hosts] == [21, 16]
    assert any("VMware-6.5-End-of-life" in w for w in p.ingest.warnings)
