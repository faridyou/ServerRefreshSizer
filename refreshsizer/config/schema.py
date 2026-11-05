"""Policy (sizing configuration) model and loader.

Precedence: defaults.yaml < project Policy sheet < --policy policy.yaml < CLI flags.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from refreshsizer.paths import CONFIG_DIR


class Growth(BaseModel):
    cpu_pct: float = 15.0
    mem_pct: float = 10.0
    storage_pct: float = 20.0
    iops_pct: float = 15.0


class GrowthEvent(BaseModel):
    year: int
    su: float = 0.0
    mem_gib: float = 0.0
    storage_tib: float = 0.0


class Policy(BaseModel):
    model_config = ConfigDict(extra="allow")

    benchmark_metric: str = "by_class"
    class_metric_map: dict[str, str] = Field(default_factory=dict)
    growth: Growth = Field(default_factory=Growth)
    growth_events: list[GrowthEvent] = Field(default_factory=list)
    horizon_years: int = 5
    u_max: dict[str, float] = Field(default_factory=lambda: {"default": 0.75})
    ha_policy: str = "auto"
    ha_n2_threshold_hosts: int = 16
    maintenance_reserve: bool = False
    max_cluster_size: int = 64
    max_hosts_per_cluster_user: int | None = None
    stretched_cluster: bool = False
    vcpu_ratio_max: dict[str, float] = Field(default_factory=dict)
    numa_nps: int = 1
    memory_basis: str = "configured"
    memory_overcommit_ratio: float = 1.25
    active_memory_factor: float = 1.25
    hypervisor_mem_overhead: float = 0.05
    mem_free_reserve: float = 0.05
    mem_bw_min_gbs_per_core: float = 4.0
    drr: dict[str, float] = Field(default_factory=lambda: {"default": 1.3})
    storage_architecture: str = "auto"
    protection: str = "vsan_esa_raid5_4p1"
    slack: float = 0.30
    min_drives_per_host: int = 4
    vsan_cpu_reserve_high_io: float = 0.10
    vsan_high_io_threshold_iops_per_host: float = 100000
    write_penalty: dict[str, float] = Field(default_factory=dict)
    iops_profile_per_class: dict[str, float] = Field(default_factory=dict)
    percentile: float = 95
    percentile_method: str = "nearest_rank"
    p95_estimate: bool = False
    p95_estimate_factor: float = 0.8
    rvtools_burst_factor: float = 1.4
    vm_peak_coincidence: float = 0.7
    saturation_threshold: float = 0.80
    saturation_uplift: float = 1.10
    inventory_assumed_util: float = 1.0
    inventory_vcpu_ratio_assumed: float = 1.0
    inventory_apply_headroom: bool = True
    k_arch: dict[str, float] = Field(default_factory=dict)
    k_arch_range: dict[str, float] = Field(default_factory=lambda: {"low": 1.10, "high": 1.25})
    k_virt_baremetal_to_virtual: float = 1.07
    rperf_to_spec_default: float = 0.425
    rperf_factor_mode: str = "conservative"
    target_virtualized: bool = True
    oracle_db_cluster_licensing: str = "active_nodes"
    vmware_edition: str = "vcf"
    vmware_min_cores_per_cpu: int = 16
    vmware_order_min_cores: int = 0
    network_vmotion_mbps: float = 1250
    network_replication_mbps: float = 0
    split_clusters: str | bool = "auto"
    prefer_measured_spec: bool = True
    objective: str = "min_tco"
    top_n: int = 5
    rack_unit_budget: int | None = None
    power_budget_w: float | None = None
    target_platforms: list[str] | None = None
    monte_carlo_runs: int = 5000
    monte_carlo_seed: int = 42
    llm_enabled: str | bool = "auto"
    llm_model: str = "claude-sonnet-5-5"
    mapping_autoaccept: float = 0.9
    mapping_yes_threshold: float = 0.6
    mapping_strict_threshold: float = 0.9
    exclude_templates: bool = True
    exclude_powered_off: bool = True
    report_lang: str = "en"

    # ---- helpers -------------------------------------------------------
    def umax(self, wl_class: str) -> float:
        return float(self.u_max.get(wl_class, self.u_max.get("default", 0.75)))

    def drr_for(self, wl_class: str) -> float:
        return float(self.drr.get(wl_class, self.drr.get("default", 1.3)))

    def iops_for(self, wl_class: str) -> float:
        p = self.iops_profile_per_class
        return float(p.get(wl_class, p.get("default", 50)))


def load_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at top level")
    return data


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def default_policy_dict() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "defaults.yaml")


def _coerce_flat(key: str, value: Any) -> tuple[list[str], Any]:
    """Policy sheet keys may be dotted ('growth.cpu_pct'); values may be strings."""
    parts = [p for p in str(key).strip().split(".") if p]
    if isinstance(value, str):
        v = value.strip()
        low = v.lower()
        if low in ("true", "yes", "oui"):
            return parts, True
        if low in ("false", "no", "non"):
            return parts, False
        if low in ("null", "none", ""):
            return parts, None
        try:
            return parts, int(v)
        except ValueError:
            pass
        try:
            return parts, float(v.replace(",", "."))
        except ValueError:
            return parts, v
    return parts, value


def flat_to_nested(flat: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in flat.items():
        parts, val = _coerce_flat(k, v)
        if not parts:
            continue
        cur = out
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = val
    return out


def load_policy(*overrides: dict[str, Any] | None) -> Policy:
    data = default_policy_dict()
    for ov in overrides:
        if ov:
            data = deep_merge(data, ov)
    return Policy.model_validate(data)


def load_prices() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "prices.yaml")
