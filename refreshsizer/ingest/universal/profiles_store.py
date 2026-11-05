"""Saved mapping profiles keyed by a header fingerprint (data/mapping_profiles/)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from refreshsizer.ingest.common import norm_header
from refreshsizer.ingest.universal.profile import TableProfile
from refreshsizer.paths import mapping_profiles_dir


class SavedMapping(BaseModel):
    column: str
    target: str | None
    unit: str | None = None


class MappingProfile(BaseModel):
    fingerprint: str
    classification: str
    headers: list[str]
    mappings: list[SavedMapping] = Field(default_factory=list)
    source: str = ""
    created: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


def fingerprint(t: TableProfile) -> str:
    key = "|".join(sorted(norm_header(c.name) for c in t.columns))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _path(fp: str, d: Path | None = None) -> Path:
    return (d or mapping_profiles_dir()) / f"{fp}.json"


def load(t: TableProfile, d: Path | None = None) -> MappingProfile | None:
    p = _path(fingerprint(t), d)
    if not p.exists():
        return None
    try:
        return MappingProfile.model_validate_json(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def save(profile: MappingProfile, d: Path | None = None) -> Path:
    p = _path(profile.fingerprint, d)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(profile.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def apply_user_edits(files: list[Path], edits: dict[tuple[str, str, str], str | None],
                     d: Path | None = None) -> int:
    """Store user-edited mappings {(file, sheet, column): target} into the matching table profiles."""
    from refreshsizer.ingest.universal.profile import profile_file

    n = 0
    for f in files:
        for t in profile_file(Path(f)).tables:
            mine = {k[2]: v for k, v in edits.items() if k[0] == t.file and k[1] == t.sheet}
            if not mine:
                continue
            prof = load(t, d) or MappingProfile(fingerprint=fingerprint(t), classification="physical_hosts",
                                                headers=[c.name for c in t.columns], source=f"{t.file} / {t.sheet}")
            by = {m.column: m for m in prof.mappings}
            for col, target in mine.items():
                if col in {c.name for c in t.columns}:
                    old = by.get(col)
                    by[col] = SavedMapping(column=col, target=target or None, unit=old.unit if old else None)
                    n += 1
            prof.mappings = list(by.values())
            save(prof, d)
    return n
