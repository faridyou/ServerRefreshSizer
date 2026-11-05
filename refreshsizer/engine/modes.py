"""Automatic sizing-mode selection per cluster (section 7.2)."""

from __future__ import annotations

from refreshsizer.model.entities import Cluster, Confidence, SizingMode

MODE_CONFIDENCE = {
    SizingMode.MEASURED: Confidence.HIGH,
    SizingMode.SUMMARY: Confidence.MEDIUM,
    SizingMode.SNAPSHOT: Confidence.LOW,
    SizingMode.INVENTORY_ONLY: Confidence.VERY_LOW,
}


def host_has_summary(c: Cluster) -> bool:
    return any(h.cpu_util_p95_pct is not None or h.cpu_util_peak_pct is not None for h in c.hosts)


def workload_has_summary(c: Cluster) -> bool:
    return any(w.physc_p95 is not None or w.db_cpu_p95_cores is not None or w.cpu_ghz_p95 is not None
               or w.cpu_ghz_peak is not None for w in c.workloads)


def select_mode(c: Cluster) -> tuple[SizingMode, Confidence, str]:
    if c.timeseries:
        hosts_ts = {p.host_id.lower() for p in c.timeseries}
        if c.hosts and not ({h.host_id.lower() for h in c.hosts} & hosts_ts):
            pass  # time series does not match hosts -> ignore
        else:
            return SizingMode.MEASURED, Confidence.HIGH, "time series available: time-aligned cluster p95"
    if host_has_summary(c) or workload_has_summary(c):
        return SizingMode.SUMMARY, Confidence.MEDIUM, "per-host/per-workload p95 or peak utilization available"
    if any(h.cpu_util_snapshot_pct is not None for h in c.hosts) or any(
            w.cpu_mhz_snapshot is not None for w in c.workloads):
        return SizingMode.SNAPSHOT, Confidence.LOW, "single point-in-time utilization (e.g. RVTools)"
    return SizingMode.INVENTORY_ONLY, Confidence.VERY_LOW, "no utilization data at all"
