"""Final ingest pass: reconciliation, sizing mode per cluster, customer question list."""

from __future__ import annotations

from refreshsizer.config.schema import Policy
from refreshsizer.engine.modes import select_mode
from refreshsizer.model.entities import DataQuality, Project, SizingMode


def finalize_project(project: Project, policy: Policy) -> Project:
    from refreshsizer.ingest.universal.reconcile import reconcile_project

    reconcile_project(project)
    project.data_quality = []
    questions: list[str] = []
    for c in project.clusters:
        mode, conf, why = select_mode(c)
        dq = DataQuality(cluster=c.name, mode=mode, confidence=conf, flags=[why])
        if mode == SizingMode.INVENTORY_ONLY:
            dq.customer_questions.append(
                f"[{c.name}] No utilization data: can you run a 7-30 day Dell Live Optics collection "
                "(or provide p95/peak CPU % per host)?")
        if mode == SizingMode.SNAPSHOT:
            dq.customer_questions.append(
                f"[{c.name}] Only a point-in-time CPU snapshot is available (RVTools). Can you provide peak/p95 "
                "CPU per host over at least 7 days (vCenter performance charts or Live Optics)?")
        if mode == SizingMode.SUMMARY and c.utilization_source == "liveoptics" and not any(
                h.cpu_util_p95_pct for h in c.hosts):
            dq.customer_questions.append(
                f"[{c.name}] Live Optics gives peak and average only: can you export the host CPU time series "
                "(timestamp, host, cpu %) so a true time-aligned p95 can be computed?")
        for h in c.hosts:
            if not h.cpu_model and h.platform_type == "x86":
                dq.customer_questions.append(f"[{c.name}] Exact CPU model of host '{h.host_id}'?")
            for a in h.assumptions:
                if "assumed from server model" in a:
                    dq.customer_questions.append(
                        f"[{c.name}] Host '{h.host_id}' ({h.model}): please confirm the installed CPU model "
                        "(currently assumed from the server model).")
            if h.cores is None and h.platform_type == "x86":
                dq.customer_questions.append(f"[{c.name}] Socket and core count of host '{h.host_id}'?")
        if c.workloads and not any(w.storage_used_gib for w in c.workloads) and not c.storage:
            dq.customer_questions.append(f"[{c.name}] Used storage capacity per VM or per datastore?")
        if not c.workloads:
            dq.customer_questions.append(
                f"[{c.name}] VM/workload list with vCPU, configured memory and used storage?")
        dq.customer_questions = list(dict.fromkeys(dq.customer_questions))
        project.data_quality.append(dq)
        questions.extend(dq.customer_questions)
    project.customer_questions = list(dict.fromkeys(questions))
    return project
