"""UI actions. Every computation goes through refreshsizer.workflow (the same functions as the CLI)."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from typing import Any

from refreshsizer import workflow
from refreshsizer.ingest.detect import detect_format
from refreshsizer.ingest.universal import profiles_store
from refreshsizer.ingest.universal.profile import profile_file
from refreshsizer.model.entities import MappingEntry
from refreshsizer.paths import mapping_profiles_dir
from refreshsizer.ui.projects import ProjectStore

Progress = Callable[[float, str], None]
FORMAT_LABEL = {"rvtools": "RVTools", "liveoptics": "Live Optics", "manual_template": "RefreshSizer template",
                "nmon": "AIX nmon", "awr": "Oracle AWR", "timeseries": "CPU time series",
                "universal": "universal (any format)"}


def file_formats(store: ProjectStore, name: str) -> list[dict[str, str]]:
    out = []
    for p in store.input_files(name):
        try:
            fmt = detect_format(p)
        except Exception as e:  # unreadable file: show it, ingest will report the error
            fmt = f"unreadable ({type(e).__name__})"
        out.append({"name": p.name, "format": fmt, "label": FORMAT_LABEL.get(fmt, fmt),
                    "size_kb": f"{p.stat().st_size / 1024:,.0f}"})
    return out


def seed_profiles_from_global(store: ProjectStore, name: str) -> int:
    """Copy saved global mapping profiles that match this project's tables into the project."""
    gdir, pdir = mapping_profiles_dir(), store.mappings_dir(name)
    n = 0
    for p in store.input_files(name):
        if detect_format(p) != "universal":
            continue
        try:
            tables = profile_file(p).tables
        except Exception:
            continue
        for t in tables:
            fp = profiles_store.fingerprint(t)
            src, dst = gdir / f"{fp}.json", pdir / f"{fp}.json"
            if src.exists() and not dst.exists():
                shutil.copy2(src, dst)
                n += 1
    return n


def save_profiles_globally(store: ProjectStore, name: str) -> int:
    n = 0
    for f in store.mappings_dir(name).glob("*.json"):
        shutil.copy2(f, mapping_profiles_dir() / f.name)
        n += 1
    return n


def merge_review(old: list[MappingEntry], new: list[MappingEntry], files: set[str]) -> list[MappingEntry]:
    """Keep the first-ingest proposals (with their original confidence) and the user's decisions."""
    keep = {(e.file, e.sheet, e.column): e for e in old if e.file in files}
    for e in new:
        k = (e.file, e.sheet, e.column)
        if e.method == "parser":
            continue
        if k not in keep:
            keep[k] = e
    return list(keep.values())


def run_ingest(store: ProjectStore, name: str, progress: Progress) -> str:
    files = store.input_files(name)
    if not files:
        raise ValueError("no input files uploaded")
    seeded = seed_profiles_from_global(store, name)
    if seeded:
        progress(0.02, f"{seeded} saved mapping profile(s) applied")
    meta = store.meta(name)
    project = workflow.ingest(files, review_mode="yes", llm="auto", name=meta.get("display", name),
                              profiles_dir=store.mappings_dir(name), progress=progress)
    store.save_project(name, project)
    store.save_review(name, merge_review(store.review(name), project.ingest.mappings, {p.name for p in files}))
    store.touch(name, stage="ingested")
    has_universal = any(m.method != "parser" for m in project.ingest.mappings)
    return f"/p/{name}/mapping" if has_universal else f"/p/{name}/summary"


def apply_mapping(store: ProjectStore, name: str, decisions: dict[tuple[str, str, str], tuple[str | None, str | None]]
                  ) -> int:
    """decisions: {(file, sheet, column): (target or None to ignore, unit)}. Stored as project profiles."""
    review = store.review(name)
    changed: dict[tuple[str, str, str], Any] = {}
    for e in review:
        k = (e.file, e.sheet, e.column)
        if k not in decisions:
            continue
        target, unit = decisions[k]
        if target != e.target or (unit or None) != (e.unit or None) or e.method != "user":
            changed[k] = (target, unit)
            e.target, e.unit, e.accepted = target, unit, target is not None
            e.method = "user"
    if changed:
        profiles_store.apply_user_edits(store.input_files(name), changed, store.mappings_dir(name))
    store.save_review(name, review)
    return len(changed)


def run_size(store: ProjectStore, name: str, overrides: dict[str, Any], label: str, progress: Progress) -> str:
    project = store.project(name)
    if project is None:
        raise ValueError("project not ingested yet")
    policy = workflow.make_policy(project.policy_overrides, store.policy_overrides(name), overrides)
    progress(0.02, "sizing")
    workflow.size(project, policy, progress=progress)
    store.save_project(name, project)
    assert project.sizing is not None
    rid = store.save_run(name, project.sizing, label, overrides)
    store.touch(name, stage="sized")
    return f"/p/{name}/results?run={rid}"


def project_for_run(store: ProjectStore, name: str, rid: str | None):  # type: ignore[no-untyped-def]
    project = store.project(name)
    if project is None:
        return None, None
    if rid:
        r = store.run(name, rid)
        if r:
            return project.model_copy(update={"sizing": r["sizing"]}), r
    runs = store.runs(name)
    if runs and project.sizing:
        return project, store.run(name, runs[0]["id"])
    return project, None


def run_reports(store: ProjectStore, name: str, rid: str | None, formats: list[str], lang: str,
                progress: Progress) -> str:
    project, _ = project_for_run(store, name, rid)
    if project is None or not project.sizing:
        raise ValueError("project not sized yet")
    paths = workflow.reports(project, store.reports_dir(name), formats, lang, progress=progress)
    workflow.save_project(project, store.reports_dir(name) / "project.json")
    store.touch(name, last_reports=[p.name for p in paths])
    return f"/p/{name}/reports"


def run_bench_update(progress: Progress, job: Any) -> str:
    from refreshsizer.benchmarks.curated_db import build, ensure_db
    from refreshsizer.benchmarks.spec_fetch import update

    cache = ensure_db()
    progress(0.1, "curated database checked")
    build(cache)
    progress(0.2, "contacting spec.org (polite crawl, 1 request/s)")
    rep = update("cpu2017", cache=cache, max_results=200)
    msg = "; ".join(rep.messages) or f"{rep.results_added} result(s) added"
    if not rep.online and not rep.skipped_recent:
        msg = ("spec.org could not be reached (offline / proxy). The curated offline database is kept and sizing "
               "works normally. " + msg)
    job.result = msg
    job.message = msg
    return f"/benchmarks?job={job.id}"
