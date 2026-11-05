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
