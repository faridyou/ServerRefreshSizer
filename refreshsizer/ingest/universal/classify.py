"""Sheet/table classification and granularity detection (section 4.6 B)."""

from __future__ import annotations

from refreshsizer.ingest.universal.profile import TableProfile

CLASSES = ["physical_hosts", "vms", "lpars", "applications", "databases", "storage_arrays", "datastores",
           "performance_timeseries", "performance_summary", "licensing", "network", "cluster_totals", "irrelevant"]
GRANULARITY = {"physical_hosts": "per host", "vms": "per VM", "lpars": "per LPAR", "databases": "per database",
               "performance_timeseries": "per host time series", "cluster_totals": "cluster totals only",
               "storage_arrays": "per array", "datastores": "per datastore"}

WEIGHTS: dict[str, dict[str, float]] = {
    "vms": {"vm_name": 2.0, "vcpu": 1.5, "ram_gb_configured": 0.5, "storage_used": 0.5, "storage_provisioned": 0.5,
            "powerstate": 0.5, "host_ref": 0.5},
    "physical_hosts": {"cpu_model": 1.5, "cpu_composite": 1.5, "sockets": 1.0, "cores_per_socket": 1.0,
                       "total_cores": 1.0, "model": 1.0, "host_id": 1.0, "ram_gb": 0.5, "nominal_ghz": 0.5,
                       "cpu_util_peak_pct": 0.5, "cpu_util_p95_pct": 0.5, "vendor": 0.5},
    "performance_timeseries": {"timestamp": 2.5, "cpu_util_avg_pct": 1.0},
    "lpars": {"physc_p95": 3.0},
    "databases": {"db_cpu_p95_cores": 3.0},
    "storage_arrays": {"array": 1.5, "storage_used": 0.75, "storage_provisioned": 0.75},
}


def classify(t: TableProfile, scores: dict[str, dict[str, float]]) -> tuple[str, float, str]:
    """Return (class, confidence 0-1, reason)."""
    if t.transposed:
        return "cluster_totals", 0.8, "key/value (transposed) table: totals for reconciliation"
    best_field: dict[str, float] = {}
    for sc in scores.values():
        for f, v in sc.items():
            best_field[f] = max(best_field.get(f, 0), v)
    totals = {}
    for cls, w in WEIGHTS.items():
        totals[cls] = sum(weight * best_field.get(f, 0) for f, weight in w.items() if best_field.get(f, 0) >= 0.6)
    if totals["storage_arrays"] and (best_field.get("cpu_model", 0) >= 0.6 or best_field.get("vcpu", 0) >= 0.6):
        totals["storage_arrays"] = 0
    if best_field.get("timestamp", 0) < 0.6:
        totals["performance_timeseries"] = 0
    cls = max(totals, key=lambda k: totals[k])
    score = totals[cls]
    if score < 1.5:
        return "irrelevant", round(min(1.0, 1 - score / 3), 3), "no host/VM/performance signature found"
    top = sorted(totals.items(), key=lambda kv: -kv[1])
    margin = top[0][1] - top[1][1]
    conf = round(min(1.0, score / 4.0) * (1.0 if margin >= 1.0 else 0.8), 3)
    return cls, conf, f"signature score {score:.2f} ({GRANULARITY.get(cls, '')})"
