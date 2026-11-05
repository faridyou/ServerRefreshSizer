"""Catalog loaders (YAML -> pydantic), cached."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import yaml

from refreshsizer.model.entities import CpuModel, TargetPlatform
from refreshsizer.paths import CATALOG_DIR


def slugify(name: str) -> str:
    s = name.lower().replace("+", "-plus")
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _load(name: str) -> dict[str, Any]:
    with open(CATALOG_DIR / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache(maxsize=1)
def load_cpus() -> dict[str, CpuModel]:
    data = _load("cpu_specs.yaml")
    defaults = data.get("defaults", {})
    out: dict[str, CpuModel] = {}
    for e in data.get("cpus", []):
        d = defaults.get(e.get("v", ""), {})
        name = str(e["m"])
        cpu = CpuModel(
            id=slugify(name), vendor=d.get("vendor", e.get("v", "")), family=str(e.get("f", "")),
            codename=str(e.get("cn", "")), model=name, cores=int(e["c"]), threads_per_core=int(e.get("t", 2)),
            base_ghz=float(e.get("b", 0) or 0), turbo_ghz=e.get("tb"), allcore_ghz=e.get("ac"),
            l3_mb=e.get("l3"), tdp_w=e.get("w"), mem_channels=e.get("ch"), max_mt_s=e.get("mts"),
            socket=e.get("s"), max_sockets=int(e.get("ms", 2)), core_type=str(e.get("ct", "other")),
            oracle_core_factor=float(e.get("ofc", d.get("ofc", 1.0))), list_price_usd=e.get("p"),
            source_url=d.get("url"), sizable=bool(e.get("sizable", True)), notes=e.get("notes"),
        )
        out[cpu.id] = cpu
    return out


@lru_cache(maxsize=1)
def load_platforms() -> dict[str, TargetPlatform]:
    data = _load("dell_17g.yaml")
    groups = data.get("cpu_groups", {})
    sizes = data.get("dimm_sizes", {})
    out: dict[str, TargetPlatform] = {}
    for p in data.get("platforms", []):
        p = dict(p)
        ids: list[str] = []
        for g in p.pop("cpu_groups", []):
            ids += [i for i in groups.get(g, []) if i not in ids]
        fam = p["cpu_family"]
        if fam == "xeon6":
            dimm = {"default": sizes["xeon6_p"], "E": sizes["xeon6_e"]}
        elif fam == "xeon6ap":
            dimm = {"default": sizes["xeon6_ap"]}
        else:
            dimm = {"default": sizes["epyc9005"]}
        p["mem_speed_by_dpc"] = {int(k): int(v) for k, v in p.get("mem_speed_by_dpc", {}).items()}
        out[p["model"]] = TargetPlatform(**p, supported_cpu_ids=ids, dimm_sizes_gb=dimm)
    return out


@lru_cache(maxsize=1)
def load_rperf() -> dict[str, Any]:
    return _load("ibm_rperf.yaml")


@lru_cache(maxsize=1)
def load_drives() -> dict[str, Any]:
    return _load("drive_options.yaml")


@lru_cache(maxsize=1)
def _oracle() -> dict[str, Any]:
    return _load("oracle_core_factor.yaml")


def oracle_core_factor(cpu_name: str | None) -> tuple[float, str]:
    data = _oracle()
    s = (cpu_name or "").lower()
    for f in data.get("factors", []):
        if f["match"] in s:
            return float(f["factor"]), f"Oracle core factor table: '{f['match']}' -> {f['factor']}"
    return float(data.get("default_factor", 1.0)), "unlisted processor -> 1.0"


@lru_cache(maxsize=1)
def load_legacy_models() -> list[dict[str, Any]]:
    return list(_load("legacy_server_cpus.yaml").get("models", []))


def legacy_model_families(model: str) -> list[str] | None:
    m = re.sub(r"\s+", " ", model.lower().replace("proliant", "").replace("poweredge", "")
               .replace("system x", "x").replace("thinksystem", "").replace("ucs", "")).strip()
    best: tuple[int, list[str]] | None = None
    for entry in load_legacy_models():
        for pat in entry["match"]:
            if re.search(r"(^|\W)" + re.escape(pat) + r"($|\W)", m):
                if best is None or len(pat) > best[0]:
                    best = (len(pat), list(entry["families"]))
    return best[1] if best else None
