"""Persistent projects: data/projects/<name>/ with input/, project.json, policy.yaml, mappings/ (mapping
decisions as profiles), mapping_review.json, reports/, runs/ (last 5 sizing runs) and meta.json."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from refreshsizer.ingest.common import CSV_EXT, SPREADSHEET_EXT
from refreshsizer.model.entities import MappingEntry, Project
from refreshsizer.paths import data_dir

ALLOWED_EXT = SPREADSHEET_EXT | CSV_EXT
MAX_RUNS = 5
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9._-]+", "-", name.strip().lower())
    s = re.sub(r"[-_.]*-[-_.]*", "-", s).strip("-.")
    return s[:64] or "project"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ProjectStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else data_dir() / "projects"
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- folders -------------------------------------------------------------------------------
    def path(self, name: str) -> Path:
        if not NAME_RE.match(name):
            raise KeyError(name)
        p = self.root / name
        if not p.is_dir():
            raise KeyError(name)
        return p

    def exists(self, name: str) -> bool:
        try:
            self.path(name)
            return True
        except KeyError:
            return False

    def _unique(self, base: str) -> str:
        name, i = base, 2
        while (self.root / name).exists():
            name = f"{base}-{i}"
            i += 1
        return name

    def create(self, display: str) -> str:
        name = self._unique(slug(display))
        p = self.root / name
        for sub in ("input", "mappings", "reports", "runs"):
            (p / sub).mkdir(parents=True)
        self.write_meta(name, {"name": name, "display": display.strip() or name, "created": now(),
                               "updated": now()})
        return name

    def duplicate(self, name: str) -> str:
        src = self.path(name)
        meta = self.meta(name)
        new = self._unique(f"{name}-copy")
        shutil.copytree(src, self.root / new)
        meta.update({"name": new, "display": f"{meta.get('display', name)} (copy)", "created": now(),
                     "updated": now()})
        self.write_meta(new, meta)
        return new

    def delete(self, name: str) -> None:
        shutil.rmtree(self.path(name))

    def list_projects(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self.root.iterdir()):
            if p.is_dir() and NAME_RE.match(p.name):
                m = self.meta(p.name)
                m["files"] = len(self.input_files(p.name))
                m["sized"] = (p / "project.json").exists() and bool(self.project(p.name) and
                                                                     self.project(p.name).sizing)  # type: ignore[union-attr]
                m["runs"] = len(self.runs(p.name))
                out.append(m)
        return sorted(out, key=lambda m: m.get("updated", ""), reverse=True)

    # ---- meta ------------------------------------------------------------------------------------
    def meta(self, name: str) -> dict[str, Any]:
        f = self.path(name) / "meta.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"name": name, "display": name}

    def write_meta(self, name: str, meta: dict[str, Any]) -> None:
        (self.root / name / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")

    def touch(self, name: str, **kw: Any) -> None:
        m = self.meta(name)
        m.update(kw)
        m["updated"] = now()
        self.write_meta(name, m)

    # ---- input files -----------------------------------------------------------------------------
    def input_dir(self, name: str) -> Path:
        d = self.path(name) / "input"
        d.mkdir(exist_ok=True)
        return d

    def input_files(self, name: str) -> list[Path]:
        return sorted(p for p in self.input_dir(name).iterdir() if p.is_file())

    def add_file(self, name: str, filename: str, data: bytes) -> Path:
        fn = Path(filename).name
        if Path(fn).suffix.lower() not in ALLOWED_EXT:
            raise ValueError(f"{fn}: unsupported file type (xlsx, xls, xlsm, ods, csv)")
        p = self.input_dir(name) / fn
        p.write_bytes(data)
        self.touch(name)
        return p

    def remove_file(self, name: str, filename: str) -> None:
        p = self.input_dir(name) / Path(filename).name
        if p.is_file():
            p.unlink()
        self.touch(name)

    # ---- project.json / policy.yaml / mapping review -------------------------------------------
    def project(self, name: str) -> Project | None:
        f = self.path(name) / "project.json"
        return Project.model_validate_json(f.read_text(encoding="utf-8")) if f.exists() else None

    def save_project(self, name: str, project: Project) -> None:
        (self.path(name) / "project.json").write_text(project.model_dump_json(indent=1), encoding="utf-8")
        self.touch(name)

    def policy_overrides(self, name: str) -> dict[str, Any]:
        f = self.path(name) / "policy.yaml"
        if not f.exists():
            return {}
        return yaml.safe_load(f.read_text(encoding="utf-8")) or {}

    def save_policy(self, name: str, overrides: dict[str, Any]) -> None:
        (self.path(name) / "policy.yaml").write_text(
            "# RefreshSizer policy overrides for this project (differences from config/defaults.yaml)\n"
            + yaml.safe_dump(overrides, sort_keys=False, allow_unicode=True), encoding="utf-8")
        self.touch(name)

    def reset_policy(self, name: str) -> None:
        f = self.path(name) / "policy.yaml"
        if f.exists():
            f.unlink()
        self.touch(name)

    def mappings_dir(self, name: str) -> Path:
        d = self.path(name) / "mappings"
        d.mkdir(exist_ok=True)
        return d

    def review(self, name: str) -> list[MappingEntry]:
        f = self.path(name) / "mapping_review.json"
        if not f.exists():
            return []
        return [MappingEntry(**e) for e in json.loads(f.read_text(encoding="utf-8"))]

    def save_review(self, name: str, entries: list[MappingEntry]) -> None:
        (self.path(name) / "mapping_review.json").write_text(
            json.dumps([e.model_dump() for e in entries], indent=1, ensure_ascii=False), encoding="utf-8")

    # ---- runs (last 5) ----------------------------------------------------------------------------
    def runs_dir(self, name: str) -> Path:
        d = self.path(name) / "runs"
        d.mkdir(exist_ok=True)
        return d

    def save_run(self, name: str, sizing: dict[str, Any], label: str, overrides: dict[str, Any]) -> str:
        rid = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        rec = {"id": rid, "created": now(), "label": label, "overrides": overrides, "sizing": sizing}
        (self.runs_dir(name) / f"{rid}.json").write_text(json.dumps(rec), encoding="utf-8")
        for old in self._run_files(name)[MAX_RUNS:]:
            old.unlink()
        return rid

    def _run_files(self, name: str) -> list[Path]:
        return sorted(self.runs_dir(name).glob("*.json"), reverse=True)

    def runs(self, name: str) -> list[dict[str, Any]]:
        out = []
        for f in self._run_files(name):
            r = json.loads(f.read_text(encoding="utf-8"))
            r.pop("sizing", None)
            out.append(r)
        return out

    def run(self, name: str, rid: str) -> dict[str, Any] | None:
        if not re.fullmatch(r"[0-9-]+", rid):
            return None
        f = self.runs_dir(name) / f"{rid}.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

    def reports_dir(self, name: str) -> Path:
        d = self.path(name) / "reports"
        d.mkdir(exist_ok=True)
        return d
