"""AIX nmon / lparstat summary CSV parser (one row per LPAR). Uses physc p95, never %entc."""

from __future__ import annotations

from pathlib import Path

from refreshsizer.ingest.base import ParseResult
from refreshsizer.ingest.common import find_col, read_csv_any, to_float, to_int, to_str
from refreshsizer.model.entities import WORKLOAD_CLASSES, SheetUse, SourceHost, SourceWorkload

COLS = {
    "lpar": ("lpar", "lpar_name", "partition"), "system_model": ("system_model", "model", "machine_type"),
    "system": ("system", "frame", "managed_system", "serial"), "system_cores": ("system_cores", "cores"),
    "ghz": ("ghz", "frequency_ghz"), "smt": ("smt", "smt_mode"), "ec": ("ec", "entitled_capacity"),
    "vp": ("vp", "virtual_processors"), "physc_p95": ("physc_p95",), "physc_max": ("physc_max",),
    "mem": ("mem_inuse_gb", "mem_gb"), "iops": ("iops_p95",), "cluster": ("cluster",),
    "workload_class": ("workload_class", "class"), "licensing": ("licensing", "license"),
    "storage_used_gib": ("storage_used_gib",),
}


def is_nmon(columns: list[str]) -> bool:
    return bool(find_col(columns, "physc_p95") and find_col(columns, "lpar") and find_col(columns, "smt"))


def parse(path: Path) -> ParseResult:
    path = Path(path)
    res = ParseResult(fmt="nmon", file=path.name, utilization_source="nmon")
    df = read_csv_any(path)
    cols = {k: find_col(df.columns, *v) for k, v in COLS.items()}
    hosts: dict[str, SourceHost] = {}
    if not cols["physc_p95"]:
        res.errors.append(f"{path.name}: physc_p95 column missing (%entc is never used)")
    for _, row in df.iterrows():
        g = {k: (row[c] if c else None) for k, c in cols.items()}
        lpar = to_str(g["lpar"])
        if not lpar:
            continue
        model = to_str(g["system_model"]) or "unknown POWER"
        cores = to_int(g["system_cores"])
        sys_id = to_str(g["system"]) or f"{model} {cores}c".strip()
        cluster = to_str(g["cluster"]) or "power"
        if sys_id.lower() not in hosts:
            hosts[sys_id.lower()] = SourceHost(
                host_id=sys_id, cluster=cluster, vendor="IBM", model=model, cpu_model=model,
                total_cores=cores, nominal_ghz=to_float(g["ghz"]), smt_mode=to_int(g["smt"]),
                platform_type="POWER", hypervisor="PowerVM", rperf_system=model)
        wl_class = (to_str(g["workload_class"]) or "").lower()
        if wl_class not in WORKLOAD_CLASSES:
            if wl_class:
                res.warnings.append(f"{lpar}: unknown workload_class '{wl_class}'")
            wl_class = "general_virt"
            res.assume(f"LPAR {lpar}: workload class not given, assumed general_virt", "workload_class", lpar)
        lic = (to_str(g["licensing"]) or "none").lower()
        mem = to_float(g["mem"])
        res.workloads.append(SourceWorkload(
            workload_id=lpar, name=lpar, host_id=sys_id, cluster=cluster, workload_class=wl_class,
            vcpu=to_float(g["vp"]), physc_p95=to_float(g["physc_p95"]), smt_mode=to_int(g["smt"]),
            system_model=model, ram_gb_configured=mem, ram_gb_active=mem, iops_p95=to_float(g["iops"]),
            storage_used_gib=to_float(g["storage_used_gib"]), licensing=lic, criticality="tier1"))
    res.hosts = list(hosts.values())
    if res.workloads:
        res.assume("nmon mem_inuse_gb used as the memory basis for LPARs (no configured value in file)",
                   "ram_gb_configured")
    res.sheets.append(SheetUse(file=path.name, sheet=path.stem, used=True, classification="lpars",
                               reason="nmon/lparstat summary", rows=len(df)))
    return res
