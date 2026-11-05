"""Oracle AWR summary CSV parser (one row per database)."""

from __future__ import annotations

from pathlib import Path

from refreshsizer.ingest.base import ParseResult
from refreshsizer.ingest.common import find_col, read_csv_any, to_float, to_int, to_str
from refreshsizer.model.entities import SheetUse, SourceHost, SourceWorkload

COLS = {
    "db": ("db", "db_name", "database"), "host": ("host", "hostname"),
    "db_cpu": ("db_cpu_s_per_s_p95", "db_cpu_p95"), "aas": ("aas_p95",), "host_cpu": ("host_cpu_pct_p95",),
    "lr": ("logical_reads_s",), "pr": ("physical_reads_s",), "redo": ("redo_mb_s",),
    "cpu_model": ("cpu_model",), "cores": ("host_cores", "cores"), "cluster": ("cluster",),
    "licensing": ("licensing", "edition"), "sga": ("sga_gb", "memory_gb"), "size": ("db_size_gib", "size_gib"),
    "iops": ("iops_p95",), "sockets": ("sockets",),
}


def is_awr(columns: list[str]) -> bool:
    return bool(find_col(columns, "db_cpu_s_per_s_p95", "db_cpu_p95") and find_col(columns, "db"))


def parse(path: Path) -> ParseResult:
    path = Path(path)
    res = ParseResult(fmt="awr", file=path.name, utilization_source="awr")
    df = read_csv_any(path)
    cols = {k: find_col(df.columns, *v) for k, v in COLS.items()}
    hosts: dict[str, SourceHost] = {}
    for _, row in df.iterrows():
        g = {k: (row[c] if c else None) for k, c in cols.items()}
        db = to_str(g["db"])
        if not db:
            continue
        host = to_str(g["host"]) or f"{db}-host"
        cluster = to_str(g["cluster"]) or "oracle"
        cpu = to_str(g["cpu_model"])
        cores = to_int(g["cores"])
        if host.lower() not in hosts:
            hosts[host.lower()] = SourceHost(
                host_id=host, cluster=cluster, cpu_model=cpu, total_cores=cores, sockets=to_int(g["sockets"]),
                cpu_util_p95_pct=None, hypervisor="bare-metal", platform_type="x86")
            if not cpu:
                res.assume(f"AWR host {host}: CPU model unknown; benchmark resolved by fallback", "cpu_model", host)
        lic = (to_str(g["licensing"]) or "oracle_ee").lower()
        if lic in ("ee", "enterprise"):
            lic = "oracle_ee"
        elif lic in ("se2", "standard"):
            lic = "oracle_se2"
        if not g["licensing"]:
            res.assume(f"DB {db}: licensing not given, assumed Oracle EE", "licensing", db)
        res.workloads.append(SourceWorkload(
            workload_id=db, name=db, host_id=host, cluster=cluster, workload_class="oltp_db",
            db_cpu_p95_cores=to_float(g["db_cpu"]), ram_gb_configured=to_float(g["sga"]),
            storage_used_gib=to_float(g["size"]), iops_p95=to_float(g["iops"]), licensing=lic,
            criticality="tier1", cpu_model=cpu, host_cores=cores))
        hc = to_float(g["host_cpu"])
        if hc is not None:
            hosts[host.lower()].cpu_util_p95_pct = max(hc, hosts[host.lower()].cpu_util_p95_pct or 0)
    res.hosts = list(hosts.values())
    res.sheets.append(SheetUse(file=path.name, sheet=path.stem, used=True, classification="databases",
                               reason="Oracle AWR summary", rows=len(df)))
    return res
