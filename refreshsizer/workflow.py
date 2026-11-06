"""The one set of entry points shared by the CLI and the web UI (no logic of its own).

ingest -> size -> reports, plus policy assembly with the documented precedence:
defaults.yaml < project Policy sheet < policy.yaml < explicit overrides (CLI flags / UI what-if)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from refreshsizer.config.schema import Policy, load_policy, load_yaml
from refreshsizer.engine.results import SizingRun
from refreshsizer.model.entities import Project

Progress = Callable[[float, str], None]


def make_policy(project_overrides: dict[str, Any] | None = None, policy_file: Path | dict[str, Any] | None = None,
                overrides: dict[str, Any] | None = None) -> Policy:
    file_ov = load_yaml(policy_file) if isinstance(policy_file, Path) else policy_file
    return load_policy(project_overrides, file_ov, overrides)


def overrides_from_flags(growth: float | None = None, years: int | None = None, objective: str | None = None,
                         p95_estimate: bool = False, lang: str | None = None,
                         u_max: float | None = None, platforms: list[str] | None = None,
                         cpu_vendors: list[str] | None = None, cpus: list[str] | None = None) -> dict[str, Any]:
    """Explicit overrides. platforms / cpu_vendors / cpus: target selection (None or [] = no restriction)."""
    ov: dict[str, Any] = {}
    if platforms:
        ov["target_platforms"] = list(platforms)
    if cpu_vendors:
        ov["target_cpu_vendors"] = [canonical_vendor(v) for v in cpu_vendors]
    if cpus:
        ov["target_cpus"] = list(cpus)
    if growth is not None:
        ov["growth"] = {"cpu_pct": growth}
    if years is not None:
        ov["horizon_years"] = years
    if objective:
        ov["objective"] = objective
    if p95_estimate:
        ov["p95_estimate"] = True
    if lang:
        ov["report_lang"] = lang
    if u_max is not None:  # one value for every workload class
        from refreshsizer.config.schema import default_policy_dict

        ov["u_max"] = {k: u_max for k in default_policy_dict().get("u_max", {"default": 0.75})}
    return ov


def canonical_vendor(v: str) -> str:
    return {"intel": "Intel", "amd": "AMD"}.get(v.strip().lower(), v.strip())


def ingest(files: list[Path], *, review_mode: str = "yes", llm: str | bool = "auto", name: str | None = None,
           profiles_dir: Path | None = None, progress: Progress | None = None) -> Project:
    from refreshsizer.ingest.pipeline import ingest_files

    return ingest_files(files, review_mode=review_mode, llm=llm, name=name, profiles_dir=profiles_dir,
                        progress=progress)


def size(project: Project, policy: Policy, progress: Progress | None = None) -> SizingRun:
    from refreshsizer.engine.sizing import size_project

    return size_project(project, policy, progress=progress)


def reports(project: Project, out: Path, formats: list[str], lang: str = "en",
            progress: Progress | None = None) -> list[Path]:
    from refreshsizer.report.build import write_reports

    return write_reports(project, out, formats, lang, progress=progress)


def load_project(path: Path) -> Project:
    return Project.model_validate_json(Path(path).read_text(encoding="utf-8"))


def save_project(project: Project, out: Path) -> None:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(project.model_dump_json(indent=1), encoding="utf-8")
