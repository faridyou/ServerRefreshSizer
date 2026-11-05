"""RVTools export parser (vInfo, vHost, vCPU, vMemory, vDisk, vPartition, vDatastore, vCluster)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from refreshsizer.ingest.base import ParseResult
from refreshsizer.ingest.common import ColumnMapper, pct_value, read_workbook, to_bool, to_float, to_int, to_str
from refreshsizer.model.entities import SheetUse, SourceHost, SourceWorkload

MIB_PER_GIB = 1024.0

VINFO = {
    "vm": ("VM", "VM Name"), "powerstate": ("Powerstate", "Power State"), "template": ("Template",),
    "cpus": ("CPUs", "Num CPU", "vCPU"), "memory": ("Memory", "Memory MiB", "Memory MB"),
    "provisioned": ("Provisioned MiB", "Provisioned MB"), "inuse": ("In Use MiB", "In Use MB"),
    "os": ("OS according to the configuration file", "OS according to the VMware Tools", "OS"),
    "host": ("Host",), "cluster": ("Cluster",), "datacenter": ("Datacenter",),
}
VHOST = {
    "host": ("Host",), "cluster": ("Cluster",), "datacenter": ("Datacenter",), "cpu_model": ("CPU Model",),
    "speed": ("Speed", "Speed MHz"), "num_cpu": ("# CPU", "CPU Sockets"), "cores_per_cpu": ("Cores per CPU",),
    "num_cores": ("# Cores",), "cpu_usage": ("CPU usage %", "CPU usage"), "memory": ("# Memory", "Memory"),
    "mem_usage": ("Memory usage %", "Memory usage"), "vcpus": ("# vCPUs",), "vram": ("vRAM",),
    "esx": ("ESX Version",), "vendor": ("Vendor",), "model": ("Model",),
    "maint": ("in Maintenance Mode", "Maintenance Mode"), "ht": ("HT Active", "Hyperthreading Active", "HT Available"),
}
VCPU = {"vm": ("VM",), "cpus": ("CPUs",), "sockets": ("Sockets",), "cps": ("Cores p/s",), "max": ("Max",),
        "overall": ("Overall",), "reservation": ("Reservation",), "limit": ("Limit",), "shares": ("Shares",)}
VMEM = {"vm": ("VM",), "size": ("Size MiB", "Size MB"), "consumed": ("Consumed",), "active": ("Active",),
        "ballooned": ("Ballooned",), "swapped": ("Swapped",), "reservation": ("Reservation",)}
VPART = {"vm": ("VM",), "disk": ("Disk",), "capacity": ("Capacity MiB",), "consumed": ("Consumed MiB",),
         "free": ("Free MiB",), "freepct": ("Free %",)}
VDISK = {"vm": ("VM",), "disk": ("Disk",), "capacity": ("Capacity MiB",), "thin": ("Thin",),
         "controller": ("Controller",)}


def _sheet(sheets: dict[str, pd.DataFrame], name: str) -> pd.DataFrame | None:
    for k, v in sheets.items():
        if k.strip().lower() == name.lower():
            return v
    return None


def parse(path: Path, exclude_templates: bool = True, exclude_powered_off: bool = True) -> ParseResult:
    path = Path(path)
    res = ParseResult(fmt="rvtools", file=path.name, utilization_source="rvtools")
    sheets = read_workbook(path)
    used = set()

    hosts: dict[str, SourceHost] = {}
    df = _sheet(sheets, "vHost")
    if df is not None:
        used.add("vHost")
        m = ColumnMapper(df, VHOST, "vHost", str(path))
        res.add_mappings(m.mapping_entries())
        res.unmapped += [f"vHost/{c}" for c in m.unmapped]
        for _, row in df.iterrows():
            hid = to_str(m.get(row, "host"))
            if not hid:
                continue
            sockets = to_int(m.get(row, "num_cpu"))
            cps = to_int(m.get(row, "cores_per_cpu"))
            total = to_int(m.get(row, "num_cores"))
            if cps is None and total and sockets:
                cps = total // sockets
            speed = to_float(m.get(row, "speed"))
            ghz = speed / 1000.0 if speed and speed > 100 else speed
            mem = to_float(m.get(row, "memory"))
            ram_gb = mem / MIB_PER_GIB if mem and mem > 16384 else mem
            ht = m.get(row, "ht")
            h = SourceHost(
                host_id=hid, cluster=to_str(m.get(row, "cluster")) or "default",
                datacenter=to_str(m.get(row, "datacenter")), vendor=to_str(m.get(row, "vendor")),
                model=to_str(m.get(row, "model")), cpu_model=to_str(m.get(row, "cpu_model")),
                sockets=sockets, cores_per_socket=cps, total_cores=total,
                threads_per_core=(2 if to_bool(ht, True) else 1) if ht is not None else None,
                nominal_ghz=ghz, ram_gb=ram_gb,
                cpu_util_snapshot_pct=pct_value(m.get(row, "cpu_usage")),
                mem_used_gb=(ram_gb * (pct_value(m.get(row, "mem_usage")) or 0) / 100.0)
                if ram_gb and m.get(row, "mem_usage") is not None else None,
                hypervisor="vSphere", in_maintenance=to_bool(m.get(row, "maint"), False),
            )
            hosts[hid.lower()] = h
        res.hosts = list(hosts.values())
        res.transformations.append("vHost: Speed MHz -> GHz; # Memory MiB -> GiB")
        res.assume("RVTools CPU usage % is a point-in-time snapshot; sizing mode SNAPSHOT with burst factor",
                   field_name="cpu_util_snapshot_pct", category="WARNING")

    vcpu_overall: dict[str, float] = {}
    df = _sheet(sheets, "vCPU")
    if df is not None:
        used.add("vCPU")
        m = ColumnMapper(df, VCPU, "vCPU", str(path))
        for _, row in df.iterrows():
            vm = to_str(m.get(row, "vm"))
            ov = to_float(m.get(row, "overall"))
            if vm and ov is not None:
                vcpu_overall[vm.lower()] = ov

    vmem: dict[str, tuple[float | None, float | None]] = {}
    df = _sheet(sheets, "vMemory")
    if df is not None:
        used.add("vMemory")
        m = ColumnMapper(df, VMEM, "vMemory", str(path))
        for _, row in df.iterrows():
            vm = to_str(m.get(row, "vm"))
            if vm:
                c = to_float(m.get(row, "consumed"))
                a = to_float(m.get(row, "active"))
                vmem[vm.lower()] = (c / MIB_PER_GIB if c is not None else None,
                                    a / MIB_PER_GIB if a is not None else None)

    part_used: dict[str, float] = {}
    df = _sheet(sheets, "vPartition")
    if df is not None:
        used.add("vPartition")
        m = ColumnMapper(df, VPART, "vPartition", str(path))
        for _, row in df.iterrows():
            vm = to_str(m.get(row, "vm"))
            c = to_float(m.get(row, "consumed"))
            if vm and c is not None:
                part_used[vm.lower()] = part_used.get(vm.lower(), 0.0) + c / MIB_PER_GIB

    disk_cap: dict[str, float] = {}
    df = _sheet(sheets, "vDisk")
    if df is not None:
        used.add("vDisk")
        m = ColumnMapper(df, VDISK, "vDisk", str(path))
        for _, row in df.iterrows():
            vm = to_str(m.get(row, "vm"))
            c = to_float(m.get(row, "capacity"))
            if vm and c is not None:
                disk_cap[vm.lower()] = disk_cap.get(vm.lower(), 0.0) + c / MIB_PER_GIB

    df = _sheet(sheets, "vInfo")
    excluded = {"template": 0, "off": 0}
    if df is not None:
        used.add("vInfo")
        m = ColumnMapper(df, VINFO, "vInfo", str(path))
        res.add_mappings(m.mapping_entries())
        res.unmapped += [f"vInfo/{c}" for c in m.unmapped]
        for _, row in df.iterrows():
            vm = to_str(m.get(row, "vm"))
            if not vm:
                continue
            tmpl = to_bool(m.get(row, "template"), False)
            on = to_bool(m.get(row, "powerstate"), True)
            if tmpl and exclude_templates:
                excluded["template"] += 1
                continue
            if not on and exclude_powered_off:
                excluded["off"] += 1
                continue
            host = to_str(m.get(row, "host"))
            cluster = to_str(m.get(row, "cluster"))
            if not cluster and host and host.lower() in hosts:
                cluster = hosts[host.lower()].cluster
            mem = to_float(m.get(row, "memory"))
            prov = to_float(m.get(row, "provisioned"))
            inuse = to_float(m.get(row, "inuse"))
            cons, act = vmem.get(vm.lower(), (None, None))
            w = SourceWorkload(
                workload_id=vm, name=vm, host_id=host, cluster=cluster or "default",
                vcpu=to_float(m.get(row, "cpus")),
                ram_gb_configured=mem / MIB_PER_GIB if mem is not None else None,
                ram_gb_consumed=cons, ram_gb_active=act,
                storage_provisioned_gib=(prov / MIB_PER_GIB) if prov is not None else disk_cap.get(vm.lower()),
                storage_used_gib=(inuse / MIB_PER_GIB) if inuse is not None else part_used.get(vm.lower()),
                cpu_mhz_snapshot=vcpu_overall.get(vm.lower()),
                os=to_str(m.get(row, "os")), powered_on=on, is_template=tmpl,
            )
            if w.os and "windows" in w.os.lower():
                w.licensing = "none"
            res.workloads.append(w)
        res.transformations.append("vInfo: Memory/Provisioned/In Use MiB -> GiB")
        if excluded["template"] or excluded["off"]:
            res.transformations.append(
                f"vInfo: excluded {excluded['template']} templates and {excluded['off']} powered-off VMs")

    for name in sheets:
        res.sheets.append(SheetUse(file=path.name, sheet=name, used=name in used,
                                   classification="rvtools" if name in used else "irrelevant",
                                   reason="RVTools tab parsed" if name in used else "not needed for sizing",
                                   rows=len(sheets[name])))
    if not res.hosts:
        res.warnings.append(f"{path.name}: no vHost tab; host capacity unknown")
    return res
