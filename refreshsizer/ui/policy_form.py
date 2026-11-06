"""Policy editor generated from the config schema (Policy model + defaults.yaml)."""

from __future__ import annotations

from typing import Any

from refreshsizer.config.schema import deep_merge, default_policy_dict, load_policy

GROUPS: list[tuple[str, list[str]]] = [
    ("ui_pg_growth", ["growth.*", "horizon_years"]),
    ("ui_pg_headroom", ["u_max.*"]),
    ("ui_pg_ha", ["ha_policy", "ha_n2_threshold_hosts", "maintenance_reserve", "max_cluster_size",
                  "max_hosts_per_cluster_user", "stretched_cluster"]),
    ("ui_pg_memory", ["memory_basis", "hypervisor_mem_overhead", "mem_free_reserve", "memory_overcommit_ratio",
                      "active_memory_factor"]),
    ("ui_pg_storage", ["storage_architecture", "protection", "slack", "drr.*", "min_drives_per_host"]),
    ("ui_pg_vcpu", ["vcpu_ratio_max.*", "numa_nps"]),
    ("ui_pg_licensing", ["vmware_edition", "vmware_min_cores_per_cpu", "vmware_order_min_cores",
                         "oracle_db_cluster_licensing", "split_clusters", "target_virtualized"]),
    ("ui_pg_objective", ["objective", "prefer_measured_spec", "top_n"]),
    ("ui_pg_util", ["percentile", "p95_estimate", "rvtools_burst_factor", "vm_peak_coincidence",
                    "saturation_threshold", "saturation_uplift"]),
    ("ui_pg_inventory", ["inventory_assumed_util", "inventory_vcpu_ratio_assumed", "inventory_apply_headroom"]),
    ("ui_pg_risc", ["rperf_factor_mode", "rperf_to_spec_default", "k_arch.*", "k_virt_baremetal_to_virtual"]),
    ("ui_pg_mc", ["monte_carlo_runs", "monte_carlo_seed"]),
]
CHOICES: dict[str, list[str]] = {
    "ha_policy": ["auto", "N+0", "N+1", "N+2"],
    "memory_basis": ["configured", "consumed", "active", "overcommit"],
    "storage_architecture": ["auto", "vsan_esa", "external"],
    "protection": ["vsan_esa_raid5_4p1", "vsan_esa_raid5_2p1", "vsan_esa_raid1", "vsan_esa_raid6_4p2"],
    "vmware_edition": ["vcf", "vvf", "none"],
    "oracle_db_cluster_licensing": ["active_nodes", "all_nodes"],
    "split_clusters": ["auto", "false"],
    "objective": ["min_tco", "min_hosts", "min_licensed_cores", "min_power", "max_headroom"],
    "rperf_factor_mode": ["conservative", "generation_matched"],
}
NULLABLE = {"max_hosts_per_cluster_user"}


def _get(d: dict[str, Any], path: str) -> Any:
    cur: Any = d
    for p in path.split("."):
        cur = cur.get(p) if isinstance(cur, dict) else None
    return cur


def _expand(patterns: list[str], defaults: dict[str, Any]) -> list[str]:
    out = []
    for pat in patterns:
        if pat.endswith(".*"):
            base = pat[:-2]
            out += [f"{base}.{k}" for k in (defaults.get(base) or {})]
        else:
            out.append(pat)
    return out


def _kind(path: str, default: Any) -> str:
    if path.split(".")[0] in CHOICES:
        return "choice"
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, int) and not isinstance(default, bool):
        return "int"
    if isinstance(default, float):
        return "float"
    if path in NULLABLE:
        return "int"
    return "text"


def form_groups(overrides: dict[str, Any], t: Any) -> list[dict[str, Any]]:
    defaults = default_policy_dict()
    current = deep_merge(defaults, overrides or {})
    groups = []
    for key, pats in GROUPS:
        fields = []
        for path in _expand(pats, defaults):
            d = _get(defaults, path)
            v = _get(current, path)
            help_key = "h_" + path.split(".")[0]
            help_txt = t(help_key)
            fields.append({"path": path, "kind": _kind(path, d), "value": v, "default": d,
                           "choices": CHOICES.get(path.split(".")[0], []), "changed": v != d,
                           "label": path, "help": "" if help_txt == help_key else help_txt})
        groups.append({"key": key, "title": t(key), "fields": fields})
    return groups


def _coerce(kind: str, raw: str, path: str) -> Any:
    raw = (raw or "").strip()
    if path in NULLABLE and raw == "":
        return None
    if kind == "bool":
        return raw.lower() in ("true", "1", "yes", "on", "oui")
    if kind == "int":
        return int(float(raw.replace(",", ".")))
    if kind == "float":
        return float(raw.replace(",", "."))
    if path == "split_clusters" and raw == "false":
        return False
    return raw


def parse_form(form: dict[str, str]) -> dict[str, Any]:
    """Form -> overrides (only the values that differ from defaults). Raises ValueError when invalid."""
    defaults = default_policy_dict()
    nested: dict[str, Any] = {}
    for _key, pats in GROUPS:
        for path in _expand(pats, defaults):
            if path not in form:
                continue
            d = _get(defaults, path)
            try:
                v = _coerce(_kind(path, d), form[path], path)
            except ValueError as e:
                raise ValueError(f"{path}: invalid value '{form[path]}'") from e
            if v == d:
                continue
            cur = nested
            parts = path.split(".")
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = v
    try:
        load_policy(nested)  # the Policy schema validates ranges
    except ValueError as e:
        from refreshsizer.ui.jobs import friendly_error

        raise ValueError(friendly_error(e)) from e
    return nested
