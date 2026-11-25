"""CPU display: 'Name (cores, GHz)' everywhere + hover details (cores/threads, base/turbo GHz, L3 cache, TDP)."""

from __future__ import annotations

from pathlib import Path

import openpyxl

from refreshsizer.catalog.cpu_info import config_label, cpu_html, cpu_label, cpu_tip, find_cpu
from refreshsizer.config.schema import load_policy
from refreshsizer.engine.sizing import size_project
from refreshsizer.ingest.pipeline import ingest_files
from refreshsizer.report.build import write_reports
from refreshsizer.report.email_prompt import build_email_prompt


def test_labels_and_tips() -> None:
    assert cpu_label("amd-epyc-9335") == "AMD EPYC 9335 (32 cores, 3.0 GHz)"
    assert cpu_label("Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz") == "Intel Xeon E5-2680 v4 (14 cores, 2.4 GHz)"
    assert cpu_label("amd-epyc-9335", lang="fr") == "AMD EPYC 9335 (32 cœurs, 3.0 GHz)"
    tip = cpu_tip("amd-epyc-9335")
    for part in ("32 cores / 64 threads", "base 3.0 GHz", "turbo 4.4 GHz", "L3 cache 128 MB", "TDP 210 W"):
        assert part in tip
    # unknown CPU: input-data values, flagged
    assert cpu_label("Mystery CPU 1", 12, 2.5) == "Mystery CPU 1 (12 cores, 2.5 GHz)"
    assert "not in the CPU catalog" in cpu_tip("Mystery CPU 1", 12, 2.5)
    assert find_cpu("Mystery CPU 1") is None
    html = str(cpu_html("amd-epyc-9335", prefix="2x "))
    assert 'class="cpu"' in html and "data-tip=" in html and "L3 cache 128 MB" in html and ">2x AMD EPYC 9335 (" in html
    assert "<script" not in str(cpu_html("<script>x</script>"))  # escaped


def test_cpu_everywhere_in_reports(fixtures_dir: Path, tmp_path: Path) -> None:
    import docx

    p = ingest_files([fixtures_dir / "liveoptics_estate.xlsx"])
    run = size_project(p, load_policy())
    rec = run.clusters[0].recommended
    assert rec
    label = cpu_label(rec.config.cpu_id)
    assert config_label(rec) == f"{rec.config.platform} {rec.config.cpu_count}x {label}"
    for level in ("customer", "presales", "full"):
        out = write_reports(p, tmp_path / level, ["html", "docx", "xlsx"], level=level)
        html = next(x for x in out if x.suffix == ".html").read_text(encoding="utf-8")
        assert label in html and "Intel Xeon E5-2680 v4 (14 cores, 2.4 GHz)" in html
        assert 'class="cpu"' in html and "L3 cache" in html and "cpu-tip" in html  # hover popup + script
        d = docx.Document(str(next(x for x in out if x.suffix == ".docx")))
        text = "\n".join(c.text for t in d.tables for r in t.rows for c in r.cells)
        assert label in text and "(14 cores, 2.4 GHz)" in text
        wb = openpyxl.load_workbook(next(x for x in out if x.suffix == ".xlsx"))
        notes = [c.comment.text for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.comment]
        assert any("L3 cache" in n and "TDP" in n for n in notes)  # Excel hover notes
    assert label in build_email_prompt(p)
