"""Reconciliation and data-quality checks (section 4.6 F)."""

from __future__ import annotations

from refreshsizer.model.entities import Project


def reconcile_project(project: Project) -> list[str]:
    warns: list[str] = []
    for c in project.clusters:
        for h in c.hosts:
            if h.sockets and h.cores_per_socket and h.total_cores and h.sockets * h.cores_per_socket != h.total_cores:
                warns.append(f"{h.host_id}: sockets x cores_per_socket ({h.sockets}x{h.cores_per_socket}) "
                             f"!= total cores {h.total_cores}; using total cores")
            if h.total_cores is None and h.sockets and h.cores_per_socket:
                h.total_cores = h.sockets * h.cores_per_socket
            if h.threads_per_core and h.threads_per_core > 8:
                warns.append(f"{h.host_id}: threads per core {h.threads_per_core} implausible")
            if h.ram_gb is not None and h.platform_type == "x86" and not (8 <= h.ram_gb <= 32768):
                warns.append(f"{h.host_id}: RAM {h.ram_gb:.0f} GB implausible for x86")
            for f in ("cpu_util_p95_pct", "cpu_util_peak_pct", "cpu_util_avg_pct", "cpu_util_snapshot_pct"):
                v = getattr(h, f)
                if v is not None and not (0 <= v <= 100):
                    warns.append(f"{h.host_id}: {f}={v} outside 0-100 %, ignored")
                    setattr(h, f, None)
        for w in c.workloads:
            if (w.storage_used_gib is not None and w.storage_provisioned_gib is not None
                    and w.storage_used_gib > w.storage_provisioned_gib * 1.001):
                warns.append(f"{w.workload_id}: storage used > provisioned")
        host_cores = sum(h.cores or 0 for h in c.hosts)
        vcpu = sum(w.vcpu or 0 for w in c.workloads)
        if host_cores and vcpu:
            ratio = vcpu / host_cores
            msg = f"[{c.name}] vCPU:pCore overcommit = {vcpu:.0f}/{host_cores} = {ratio:.2f}"
            project.ingest.transformations.append(msg)
            if ratio > 10:
                warns.append(msg + " (unusually high)")
        host_ram = sum(h.ram_gb or 0 for h in c.hosts)
        vram = sum(w.ram_gb_configured or 0 for w in c.workloads)
        if host_ram and vram:
            project.ingest.transformations.append(
                f"[{c.name}] vRAM:pRAM = {vram:.0f}/{host_ram:.0f} GB = {vram / host_ram:.2f}")
            if vram > host_ram * 1.5:
                warns.append(f"[{c.name}] configured VM memory exceeds host RAM by >50 % (overcommit)")
    project.ingest.warnings.extend(warns)
    return warns
