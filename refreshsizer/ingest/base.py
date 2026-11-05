"""Parser result container and project assembly."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from refreshsizer.model.entities import (
    Assumption,
    MappingEntry,
    Project,
    SheetUse,
    SourceHost,
    SourceWorkload,
    StorageArray,
    TimeSeriesPoint,
)


@dataclass
class ParseResult:
    fmt: str
    file: str
    hosts: list[SourceHost] = field(default_factory=list)
    workloads: list[SourceWorkload] = field(default_factory=list)
    storage: list[StorageArray] = field(default_factory=list)
    timeseries: list[TimeSeriesPoint] = field(default_factory=list)
    mappings: list[MappingEntry] = field(default_factory=list)
    sheets: list[SheetUse] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    transformations: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    policy: dict[str, Any] = field(default_factory=dict)
    utilization_source: str | None = None

    def assume(self, text: str, field_name: str | None = None, entity: str | None = None,
               category: str = "ASSUMPTION") -> None:
        self.assumptions.append(Assumption(text=text, field=field_name, entity=entity, category=category))

    def add_mappings(self, entries: list[dict[str, Any]]) -> None:
        self.mappings.extend(MappingEntry(**e) for e in entries)


def assemble(results: list[ParseResult], name: str = "RefreshSizer project") -> Project:
    """Merge parser outputs into one Project; hosts/workloads are grouped by cluster name."""
    proj = Project(name=name)
    host_index: dict[str, SourceHost] = {}
    for r in results:
        proj.ingest.files.append(r.file)
        proj.ingest.formats[r.file] = r.fmt
        proj.ingest.sheets.extend(r.sheets)
        proj.ingest.mappings.extend(r.mappings)
        proj.ingest.assumptions.extend(r.assumptions)
        proj.ingest.warnings.extend(r.warnings)
        proj.ingest.errors.extend(r.errors)
        proj.ingest.transformations.extend(r.transformations)
        proj.ingest.unmapped_columns.extend(r.unmapped)
        proj.policy_overrides.update(r.policy)
        for h in r.hosts:
            key = h.host_id.lower()
            if key in host_index:
                # merge: keep existing values, fill gaps from the new record
                existing = host_index[key]
                for fname, val in h.model_dump().items():
                    if val not in (None, [], "") and getattr(existing, fname, None) in (None, [], ""):
                        setattr(existing, fname, val)
                proj.ingest.conflicts.append(
                    f"Host '{h.host_id}' present in several files; records merged (first file wins on conflicts)")
                continue
            host_index[key] = h
            proj.cluster(h.cluster).hosts.append(h)
            if r.utilization_source:
                c = proj.cluster(h.cluster)
                c.utilization_source = c.utilization_source or r.utilization_source
        for w in r.workloads:
            if w.host_id and w.host_id.lower() in host_index:
                w.cluster = host_index[w.host_id.lower()].cluster
            c = proj.cluster(w.cluster)
            c.workloads.append(w)
            if r.utilization_source:
                c.utilization_source = c.utilization_source or r.utilization_source
        for s in r.storage:
            proj.cluster(s.cluster).storage.append(s)
        for p in r.timeseries:
            h2 = host_index.get(p.host_id.lower())
            proj.cluster(h2.cluster if h2 else "default").timeseries.append(p)
    proj.clusters = [c for c in proj.clusters if c.hosts or c.workloads or c.storage]
    return proj
