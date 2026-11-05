"""Dell Live Optics (Optical Prime) parser: VMware Excel and General Excel variants (base-2 units)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from refreshsizer.ingest.base import ParseResult
from refreshsizer.ingest.common import (
    ColumnMapper,
    find_col,
    pct_value,
    read_csv_any,
    read_workbook,
    to_bool,
    to_float,
    to_int,
    to_str,
)
from refreshsizer.model.entities import SheetUse, SourceHost, SourceWorkload, TimeSeriesPoint

KIB_PER_GIB = 1024.0 * 1024.0
MIB_PER_GIB = 1024.0

ESX_HOSTS = {
    "host": ("Host-Name", "Host Name", "Hostname"), "datacenter": ("Datacenter",), "vcenter": ("vCenter",),
    "cluster": ("Cluster",), "vendor": ("Manufacturer",), "model": ("Model",),
    "sockets": ("CPU Sockets",), "cores": ("CPU Cores",), "cpu_desc": ("CPU Description",),
    "ghz": ("CPU Clock Speed (GHz)", "CPU Clock Speed"), "threads": ("CPU Threads",),
    "mem_kib": ("Memory (KiB)",), "vm_count": ("Guest VM Count",),
}
ESX_PERF = {
    "host": ("Host-Name", "Host Name"), "peak_pct": ("Peak CPU %", "Peak CPU Percentage"),
    "peak_ghz": ("Peak CPU (GHz)",), "avg_pct": ("Average CPU %", "Average CPU Percentage"),
    "avg_ghz": ("Average CPU (GHz)",), "p95_pct": ("95% CPU %", "95th Percentile CPU %", "CPU 95%"),
    "peak_mem_pct": ("Peak Memory %",), "peak_mem_mib": ("Peak Memory (MiB)",),
    "avg_mem_pct": ("Average Memory %",), "peak_iops": ("Peak IOPS",), "avg_iops": ("Average IOPS",),
    "p95_iops": ("95% IOPS",), "rw": ("Average Read/Write Ratio",), "peak_mbps": ("Peak Throughput MB/s",),
    "avg_mbps": ("Avg. Throughput MB/s", "Average Throughput MB/s"),
}
VM_INV = {
    "vm": ("VM Name",), "running": ("IsRunning",), "power": ("PowerState",), "vcpu": ("Virtual CPU",),
    "prov_mem": ("Provisioned Memory (MiB)",), "active_mem": ("Used Memory (active) (MiB)",),
    "consumed_mem": ("Consumed Memory (MiB)",), "guest_cap": ("Guest VM Disk Capacity (MiB)",),
    "guest_used": ("Guest VM Disk Used (MiB)",), "vdisk_size": ("Virtual Disk Size (MiB)",),
    "vdisk_used": ("Virtual Disk Used (MiB)",), "template": ("Template",), "cluster": ("Cluster",),
    "host": ("Host",), "datastore": ("Datastore",), "os": ("Guest OS", "OS"),
}
VM_PERF = {
    "vm": ("VM Name",), "peak_pct": ("Peak vCPU %",), "peak_ghz": ("Peak vCPU (GHz)",),
    "avg_pct": ("Average vCPU %",), "avg_ghz": ("Average vCPU (GHz)",), "peak_mem": ("Peak Memory (MiB)",),
    "peak_iops": ("Peak IOPS",), "avg_iops": ("Average IOPS",), "read_iops": ("Peak Read IOPS",),
    "write_iops": ("Peak Write IOPS",), "peak_mbps": ("Peak Throughput MB/s", "Peak MB/s"),
}
GEN_SERVERS = {
    "host": ("Server Name", "Name", "Hostname", "Host-Name"), "cluster": ("Cluster",),
    "vendor": ("Manufacturer",), "model": ("Model",), "sockets": ("CPU Sockets",), "cores": ("CPU Cores",),
    "cpu_desc": ("CPU Description", "CPU Model"), "ghz": ("CPU Clock Speed (GHz)",),
    "mem_kib": ("Memory (KiB)",), "peak_pct": ("Peak CPU Percentage", "Peak CPU %"),
    "avg_pct": ("Average CPU Percentage",), "peak_mem_kib": ("Peak Memory Usage (KiB)",),
    "p95_iops": ("95% IOPS",), "peak_iops": ("Peak IOPS",), "os": ("OS", "Operating System"),
}


def _find_sheet(sheets: dict[str, pd.DataFrame], *names: str) -> tuple[str, pd.DataFrame] | None:
    low = {k.strip().lower(): k for k in sheets}
    for n in names:
        if n.lower() in low:
            k = low[n.lower()]
            return k, sheets[k]
    return None


def _find_vm_inventory(sheets: dict[str, pd.DataFrame]) -> tuple[str, pd.DataFrame] | None:
    for k, df in sheets.items():
        cols = df.columns
        if find_col(cols, "VM Name") and find_col(cols, "Virtual CPU") and find_col(cols, "Provisioned Memory (MiB)"):
            return k, df
    return None


def parse_timeseries_csv(path: Path) -> list[TimeSeriesPoint]:
    """Time-series CSV: timestamp, host, cpu_pct (0-100 or 0-1)."""
    df = read_csv_any(Path(path))
    ts_c = find_col(df.columns, "timestamp", "time", "ts", "datetime", "date")
    h_c = find_col(df.columns, "host", "hostname", "host-name", "host name")
    u_c = find_col(df.columns, "cpu_pct", "cpu %", "cpu", "cpu usage %", "cpu_util")
    if not (ts_c and h_c and u_c):
        raise ValueError(f"{path}: time-series CSV needs timestamp, host, cpu_pct columns")
    out = []
    for _, row in df.iterrows():
        u = pct_value(row[u_c])
        if u is None:
            continue
        out.append(TimeSeriesPoint(ts=str(row[ts_c]), host_id=str(row[h_c]).strip(), cpu_pct=u))
    return out


def parse(path: Path, timeseries: Path | None = None, exclude_templates: bool = True,
          exclude_powered_off: bool = True) -> ParseResult:
    path = Path(path)
    res = ParseResult(fmt="liveoptics", file=path.name, utilization_source="liveoptics")
    sheets = read_workbook(path)
    used: set[str] = set()
    hosts: dict[str, SourceHost] = {}

    hs = _find_sheet(sheets, "ESX Hosts", "Hypervisors")
    general = _find_sheet(sheets, "Servers")
    if hs is not None:
        name, df = hs
        used.add(name)
        m = ColumnMapper(df, ESX_HOSTS, name, str(path))
        res.add_mappings(m.mapping_entries())
        res.unmapped += [f"{name}/{c}" for c in m.unmapped]
        for _, row in df.iterrows():
            hid = to_str(m.get(row, "host"))
            if not hid:
                continue
            sockets = to_int(m.get(row, "sockets"))
            cores = to_int(m.get(row, "cores"))
            threads = to_int(m.get(row, "threads"))
            mem_kib = to_float(m.get(row, "mem_kib"))
            hosts[hid.lower()] = SourceHost(
                host_id=hid, cluster=to_str(m.get(row, "cluster")) or "default",
                datacenter=to_str(m.get(row, "datacenter")), vendor=to_str(m.get(row, "vendor")),
                model=to_str(m.get(row, "model")), cpu_model=to_str(m.get(row, "cpu_desc")),
                sockets=sockets, total_cores=cores,
                cores_per_socket=(cores // sockets) if cores and sockets else None,
                threads_per_core=(threads // cores) if threads and cores else None,
                nominal_ghz=to_float(m.get(row, "ghz")),
                ram_gb=mem_kib / KIB_PER_GIB if mem_kib is not None else None,
                hypervisor="vSphere",
            )
        res.transformations.append(f"{name}: Memory (KiB) -> GiB (base-2)")
    elif general is not None:
        name, df = general
        used.add(name)
        m = ColumnMapper(df, GEN_SERVERS, name, str(path))
        res.add_mappings(m.mapping_entries())
        for _, row in df.iterrows():
            hid = to_str(m.get(row, "host"))
            if not hid:
                continue
            sockets = to_int(m.get(row, "sockets"))
            cores = to_int(m.get(row, "cores"))
            mem_kib = to_float(m.get(row, "mem_kib"))
            pk = to_float(m.get(row, "peak_mem_kib"))
            hosts[hid.lower()] = SourceHost(
                host_id=hid, cluster=to_str(m.get(row, "cluster")) or "default",
                vendor=to_str(m.get(row, "vendor")), model=to_str(m.get(row, "model")),
                cpu_model=to_str(m.get(row, "cpu_desc")), sockets=sockets, total_cores=cores,
                cores_per_socket=(cores // sockets) if cores and sockets else None,
                nominal_ghz=to_float(m.get(row, "ghz")),
                ram_gb=mem_kib / KIB_PER_GIB if mem_kib is not None else None,
                mem_used_gb=pk / KIB_PER_GIB if pk is not None else None,
                cpu_util_peak_pct=pct_value(m.get(row, "peak_pct")),
                cpu_util_avg_pct=pct_value(m.get(row, "avg_pct")),
                iops_p95=to_float(m.get(row, "p95_iops")), iops_peak=to_float(m.get(row, "peak_iops")),
                hypervisor="bare-metal",
            )
        res.transformations.append(f"{name}: General Excel; Memory (KiB) -> GiB; 95% IOPS is nearest-rank")

    ps = _find_sheet(sheets, "ESX Performance")
    if ps is not None:
        name, df = ps
        used.add(name)
        m = ColumnMapper(df, ESX_PERF, name, str(path))
        res.add_mappings(m.mapping_entries())
        res.unmapped += [f"{name}/{c}" for c in m.unmapped]
        for _, row in df.iterrows():
            hid = to_str(m.get(row, "host"))
            if not hid:
                continue
            h = hosts.get(hid.lower())
            if h is None:
                res.warnings.append(f"ESX Performance: host '{hid}' not in ESX Hosts (orphan)")
                continue
            h.cpu_util_peak_pct = pct_value(m.get(row, "peak_pct"))
            h.cpu_util_avg_pct = pct_value(m.get(row, "avg_pct"))
            p95 = pct_value(m.get(row, "p95_pct"))
            if p95 is not None:
                h.cpu_util_p95_pct = p95
            pm = to_float(m.get(row, "peak_mem_mib"))
            if pm is not None:
                h.mem_used_gb = pm / MIB_PER_GIB
            h.iops_peak = to_float(m.get(row, "peak_iops"))
            h.iops_avg = to_float(m.get(row, "avg_iops"))
            h.iops_p95 = to_float(m.get(row, "p95_iops"))
            h.throughput_mbps_peak = to_float(m.get(row, "peak_mbps"))
        res.assume("Live Optics VMware Excel has no p95 CPU column: host PEAK CPU % is used (conservative) "
                   "unless a time series or --p95-estimate is supplied", field_name="cpu_util_peak_pct",
                   category="WARNING")
    res.hosts = list(hosts.values())

    vm_perf: dict[str, dict[str, float | None]] = {}
    vp = _find_sheet(sheets, "VM Performance")
    if vp is not None:
        name, df = vp
        used.add(name)
        m = ColumnMapper(df, VM_PERF, name, str(path))
        for _, row in df.iterrows():
            vm = to_str(m.get(row, "vm"))
            if vm:
                vm_perf[vm.lower()] = {
                    "peak_ghz": to_float(m.get(row, "peak_ghz")), "avg_ghz": to_float(m.get(row, "avg_ghz")),
                    "peak_iops": to_float(m.get(row, "peak_iops")), "peak_mbps": to_float(m.get(row, "peak_mbps")),
                }

    inv = _find_vm_inventory(sheets) or _find_sheet(sheets, "VMs")
    if inv is not None:
        name, df = inv
        used.add(name)
        m = ColumnMapper(df, VM_INV, name, str(path))
        res.add_mappings(m.mapping_entries())
        res.unmapped += [f"{name}/{c}" for c in m.unmapped]
        for _, row in df.iterrows():
            vm = to_str(m.get(row, "vm"))
            if not vm:
                continue
            running = m.get(row, "running")
            on = to_bool(running, True) if running is not None else to_bool(m.get(row, "power"), True)
            tmpl = to_bool(m.get(row, "template"), False)
            if (tmpl and exclude_templates) or (not on and exclude_powered_off):
                continue
            host = to_str(m.get(row, "host"))
            cluster = to_str(m.get(row, "cluster"))
            if not cluster and host and host.lower() in hosts:
                cluster = hosts[host.lower()].cluster
            def gib(key: str) -> float | None:
                v = to_float(m.get(row, key))
                return v / MIB_PER_GIB if v is not None else None
            used_gib = gib("guest_used")
            if used_gib is None:
                used_gib = gib("vdisk_used")
            prov_gib = gib("vdisk_size")
            if prov_gib is None:
                prov_gib = gib("guest_cap")
            perf = vm_perf.get(vm.lower(), {})
            res.workloads.append(SourceWorkload(
                workload_id=vm, name=vm, host_id=host, cluster=cluster or "default",
                vcpu=to_float(m.get(row, "vcpu")), ram_gb_configured=gib("prov_mem"),
                ram_gb_active=gib("active_mem"), ram_gb_consumed=gib("consumed_mem"),
                storage_used_gib=used_gib, storage_provisioned_gib=prov_gib,
                cpu_ghz_peak=perf.get("peak_ghz"), cpu_ghz_avg=perf.get("avg_ghz"),
                iops_peak=perf.get("peak_iops"), throughput_mbps_p95=perf.get("peak_mbps"),
                os=to_str(m.get(row, "os")), powered_on=on, is_template=tmpl,
            ))
        res.transformations.append(f"{name}: MiB -> GiB (base-2)")

    if timeseries is not None:
        res.timeseries = parse_timeseries_csv(timeseries)
        res.transformations.append(f"time series {Path(timeseries).name}: {len(res.timeseries)} points")

    for name in sheets:
        res.sheets.append(SheetUse(file=path.name, sheet=name, used=name in used,
                                   classification="liveoptics" if name in used else "irrelevant",
                                   reason="Live Optics tab parsed" if name in used else "not needed for sizing",
                                   rows=len(sheets[name])))
    return res
