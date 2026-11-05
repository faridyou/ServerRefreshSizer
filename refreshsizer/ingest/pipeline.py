"""Ingest orchestration: detect each file's format, parse, assemble the Project."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from refreshsizer.ingest import awr, liveoptics, manual_template, nmon, rvtools
from refreshsizer.ingest.base import ParseResult, assemble
from refreshsizer.ingest.detect import detect_format
from refreshsizer.model.entities import Project

log = logging.getLogger("refreshsizer.ingest")


def ingest_files(paths: list[Path], *, review_mode: str = "yes", llm: str | bool = "auto",
                 policy_overrides: dict[str, Any] | None = None, name: str | None = None,
                 llm_client: Any = None, interactive_io: Any = None) -> Project:
    """review_mode: 'interactive' | 'yes' | 'strict'."""
    from refreshsizer.config.schema import load_policy

    policy = load_policy(policy_overrides)
    paths = [Path(p) for p in paths]
    formats = {p: detect_format(p) for p in paths}
    ts_files = [p for p, f in formats.items() if f == "timeseries"]
    results: list[ParseResult] = []
    universal_files: list[Path] = []
    for p, fmt in formats.items():
        log.info("%s: detected format %s", p.name, fmt)
        if fmt == "rvtools":
            results.append(rvtools.parse(p, policy.exclude_templates, policy.exclude_powered_off))
        elif fmt == "liveoptics":
            results.append(liveoptics.parse(p, ts_files[0] if ts_files else None,
                                            policy.exclude_templates, policy.exclude_powered_off))
        elif fmt == "manual_template":
            results.append(manual_template.parse(p))
        elif fmt == "nmon":
            results.append(nmon.parse(p))
        elif fmt == "awr":
            results.append(awr.parse(p))
        elif fmt == "timeseries":
            if not any(f == "liveoptics" for f in formats.values()):
                r = ParseResult(fmt="timeseries", file=p.name)
                r.timeseries = liveoptics.parse_timeseries_csv(p)
                results.append(r)
        else:
            universal_files.append(p)
    if universal_files:
        from refreshsizer.ingest.universal.pipeline import universal_ingest

        results.extend(universal_ingest(universal_files, policy=policy, review_mode=review_mode, llm=llm,
                                        llm_client=llm_client, io=interactive_io))
    project = assemble(results, name=name or (paths[0].stem if paths else "project"))
    if any(r.fmt.startswith("universal") and r.utilization_source for r in results):
        pass
    from refreshsizer.ingest.universal.summary import finalize_project

    finalize_project(project, policy)
    return project
