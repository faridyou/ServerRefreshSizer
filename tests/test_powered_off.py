"""Powered-off VMs / templates: excluded from CPU and memory, but their storage is kept (it migrates)."""

from __future__ import annotations

from pathlib import Path

import pytest

from refreshsizer.config.schema import load_policy
from refreshsizer.engine.sizing import size_project
from refreshsizer.ingest.pipeline import ingest_files
from refreshsizer.ingest.universal.summary import estate_summary
from refreshsizer.report.build import write_reports
from refreshsizer.report.email_prompt import build_email_prompt
from tests.baseline import VSAN

RV = "rvtools_estate.xlsx"  # 120 powered-on + 8 powered-off VMs


def test_inactive_kept_aside_at_ingest(fixtures_dir: Path) -> None:
    p = ingest_files([fixtures_dir / RV], llm=False)
    c = p.clusters[0]
    assert len(c.workloads) == 120 and len(c.inactive_workloads) == 8
    assert all(not w.powered_on for w in c.inactive_workloads)
    row = estate_summary(p)[0]
    assert row["vms"] == 120 and row["off"]["count"] == 8 and row["off"]["used_tib"] > 0
    assert any("powered-off VMs" in q and "deleted" in q for q in p.customer_questions)
    assert any("powered-off VMs from CPU/memory" in x for x in p.ingest.transformations)


def test_storage_includes_powered_off_but_cpu_memory_do_not(fixtures_dir: Path) -> None:
    p1 = ingest_files([fixtures_dir / RV], llm=False)
    off_tib = sum((w.storage_used_gib if w.storage_used_gib is not None else (w.storage_provisioned_gib or 0))
                  for w in p1.clusters[0].inactive_workloads) / 1024  # snapshot mode: used capacity
    on = size_project(p1, load_policy(VSAN)).clusters[0].demand
    p2 = ingest_files([fixtures_dir / RV], llm=False)
    no = size_project(p2, load_policy(VSAN, {"storage_include_powered_off": False})).clusters[0].demand
    assert on.vm_count == no.vm_count == 120 and on.total_vcpu == no.total_vcpu and on.m_t == no.m_t
    assert on.d0 == no.d0  # CPU unchanged
    assert on.inactive_vms == 8 and on.inactive_storage_tib > 0 and no.inactive_storage_tib == 0
    assert on.storage_used0_tib - no.storage_used0_tib == pytest.approx(off_tib, rel=1e-3)


def test_reports_and_prompt_mention_it(fixtures_dir: Path, tmp_path: Path) -> None:
    p = ingest_files([fixtures_dir / RV], llm=False)
    size_project(p, load_policy(VSAN))
    html = write_reports(p, tmp_path, ["html"], level="presales")[0].read_text(encoding="utf-8")
    assert "of which powered-off VMs / templates" in html
    assert "powered-off VMs / templates, kept because their disks migrate" in build_email_prompt(p)
