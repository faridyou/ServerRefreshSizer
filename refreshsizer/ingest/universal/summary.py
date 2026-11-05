"""Final ingest pass (reconciliation, sizing mode per cluster, customer questions EN/FR) and the ingest summary."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table

from refreshsizer.config.schema import Policy
from refreshsizer.engine.modes import select_mode
from refreshsizer.model.entities import Cluster, DataQuality, Project, SizingMode

Q: dict[str, dict[str, str]] = {
    "no_util": {
        "en": "[{c}] No utilization data: can you run a 7-30 day Dell Live Optics collection (or provide p95/peak "
              "CPU % per host)?",
        "fr": "[{c}] Aucune donnée d'utilisation : pouvez-vous lancer une collecte Dell Live Optics de 7 à 30 jours "
              "(ou fournir le CPU p95/pic en % par hôte) ?"},
    "snapshot": {
        "en": "[{c}] Only a point-in-time CPU snapshot is available (RVTools). Can you provide peak/p95 CPU per host "
              "over at least 7 days (vCenter performance charts or Live Optics)?",
        "fr": "[{c}] Seule une mesure CPU instantanée est disponible (RVTools). Pouvez-vous fournir le CPU pic/p95 "
              "par hôte sur au moins 7 jours (graphiques vCenter ou Live Optics) ?"},
    "lo_ts": {
        "en": "[{c}] Live Optics gives peak and average only: can you export the host CPU time series (timestamp, "
              "host, cpu %) so a true time-aligned p95 can be computed?",
        "fr": "[{c}] Live Optics ne donne que le pic et la moyenne : pouvez-vous exporter la série temporelle CPU par "
              "hôte (horodatage, hôte, cpu %) pour calculer un vrai p95 aligné dans le temps ?"},
    "cpu": {"en": "[{c}] Exact CPU model of host '{h}'?", "fr": "[{c}] Modèle exact de processeur de l'hôte '{h}' ?"},
    "cpu_assumed": {
        "en": "[{c}] Host '{h}' ({m}): please confirm the installed CPU model (currently assumed from the server "
              "model).",
        "fr": "[{c}] Hôte '{h}' ({m}) : merci de confirmer le modèle de processeur installé (actuellement déduit du "
              "modèle de serveur)."},
    "cores": {"en": "[{c}] Socket and core count of host '{h}'?",
              "fr": "[{c}] Nombre de sockets et de cœurs de l'hôte '{h}' ?"},
    "storage": {"en": "[{c}] Used storage capacity per VM or per datastore?",
                "fr": "[{c}] Capacité de stockage utilisée par VM ou par datastore ?"},
    "vms": {"en": "[{c}] VM/workload list with vCPU, configured memory and used storage?",
            "fr": "[{c}] Liste des VM/charges avec vCPU, mémoire configurée et stockage utilisé ?"},
    "growth": {"en": "Expected annual growth (CPU, memory, storage) and planning horizon? (default 15/10/20 %, "
                     "5 years)",
               "fr": "Croissance annuelle attendue (CPU, mémoire, stockage) et horizon de planification ? (défaut "
                     "15/10/20 %, 5 ans)"},
}


def _q(key: str, **kw: Any) -> tuple[str, str]:
    return Q[key]["en"].format(**kw), Q[key]["fr"].format(**kw)


def cluster_questions(c: Cluster, mode: SizingMode) -> list[tuple[str, str]]:
    qs: list[tuple[str, str]] = []
    if mode == SizingMode.INVENTORY_ONLY:
        qs.append(_q("no_util", c=c.name))
    if mode == SizingMode.SNAPSHOT:
        qs.append(_q("snapshot", c=c.name))
    if mode == SizingMode.SUMMARY and c.utilization_source == "liveoptics" and not any(
            h.cpu_util_p95_pct for h in c.hosts):
        qs.append(_q("lo_ts", c=c.name))
    for h in c.hosts:
        if any("server model" in a for a in h.assumptions):
            qs.append(_q("cpu_assumed", c=c.name, h=h.host_id, m=h.model))
        elif not h.cpu_model and h.platform_type == "x86":
            qs.append(_q("cpu", c=c.name, h=h.host_id))
        if h.cores is None and h.platform_type == "x86":
            qs.append(_q("cores", c=c.name, h=h.host_id))
    if c.workloads and not any(w.storage_used_gib or w.storage_provisioned_gib for w in c.workloads) \
            and not c.storage:
        qs.append(_q("storage", c=c.name))
    if not c.workloads:
        qs.append(_q("vms", c=c.name))
    return list(dict.fromkeys(qs))


def finalize_project(project: Project, policy: Policy) -> Project:
    from refreshsizer.ingest.universal.reconcile import reconcile_project

    reconcile_project(project)
    project.data_quality = []
    en: list[str] = []
    fr: list[str] = []
    for c in project.clusters:
        mode, conf, why = select_mode(c)
        dq = DataQuality(cluster=c.name, mode=mode, confidence=conf, flags=[why])
        qs = cluster_questions(c, mode)
        dq.customer_questions = [q[0] for q in qs]
        project.data_quality.append(dq)
        en += [q[0] for q in qs]
        fr += [q[1] for q in qs]
    g = _q("growth")
    project.customer_questions = list(dict.fromkeys(en + [g[0]]))
    project.customer_questions_fr = list(dict.fromkeys(fr + [g[1]]))
    return project


def estate_summary(project: Project) -> list[dict[str, Any]]:
    from refreshsizer.benchmarks.spc import resolve_spc

    rows = []
    dq = {d.cluster: d for d in project.data_quality}
    for c in project.clusters:
        cpus: dict[str, str] = {}
        for h in c.hosts:
            if h.cpu_model and h.cpu_model not in cpus:
                if h.platform_type == "POWER":
                    cpus[h.cpu_model] = "IBM rPerf"
                else:
                    r = resolve_spc(h.cpu_model, chips=h.sockets)
                    cpus[h.cpu_model] = f"{r.cpu_name}: SPC {r.spc:.2f} ({r.method})"
        d = dq.get(c.name)
        rows.append({
            "cluster": c.name, "hosts": len(c.hosts), "sockets": sum(h.sockets or 0 for h in c.hosts),
            "cores": sum(h.cores or 0 for h in c.hosts), "cpu_models": cpus,
            "ram_gb": round(sum(h.ram_gb or 0 for h in c.hosts), 1), "vms": len(c.workloads),
            "vcpu": round(sum(w.vcpu or 0 for w in c.workloads), 1),
            "vram_gb": round(sum(w.ram_gb_configured or 0 for w in c.workloads), 1),
            "storage_prov_tib": round(sum(w.storage_provisioned_gib or 0 for w in c.workloads) / 1024, 2),
            "storage_used_tib": round(sum(w.storage_used_gib or 0 for w in c.workloads) / 1024, 2),
            "iops": round(sum((h.iops_p95 or h.iops_peak or 0) for h in c.hosts)
                          or sum((w.iops_p95 or 0) for w in c.workloads)),
            "mode": d.mode.value if d else "", "confidence": d.confidence.value if d else ""})
    return rows


def render_ingest_summary(project: Project, console: Console | None = None, lang: str = "en") -> None:
    con = console or Console()
    ing = project.ingest
    con.rule("[bold]Ingest summary")
    con.print(f"Files: {', '.join(f'{f} ({ing.formats.get(f, "?")})' for f in ing.files)}")
    t = Table(title="Sheets")
    for h in ("file", "sheet", "used", "class", "rows", "reason"):
        t.add_column(h)
    for s in ing.sheets:
        t.add_row(s.file, s.sheet, "yes" if s.used else "no", s.classification, str(s.rows), s.reason[:70])
    con.print(t)
    low = [m for m in ing.mappings if m.method not in ("parser",)]
    if low:
        mt = Table(title="Column mappings (universal ingest)")
        for h in ("sheet", "column", "field", "conf.", "method", "sample", "transformed"):
            mt.add_column(h)
        for m in low:
            mt.add_row(m.sheet, m.column, m.target or "-", f"{m.confidence:.2f}", m.method, (m.sample or "")[:25],
                       (m.transformed or "")[:25])
        con.print(mt)
    et = Table(title="Estate summary")
    for h in ("cluster", "hosts", "sockets", "cores", "RAM GB", "VMs", "vCPU", "vRAM GB", "prov TiB", "used TiB",
              "IOPS", "mode", "confidence"):
        et.add_column(h)
    rows = estate_summary(project)
    for r in rows:
        et.add_row(r["cluster"], str(r["hosts"]), str(r["sockets"]), str(r["cores"]), f"{r['ram_gb']:,.0f}",
                   str(r["vms"]), f"{r['vcpu']:,.0f}", f"{r['vram_gb']:,.0f}", f"{r['storage_prov_tib']:,.1f}",
                   f"{r['storage_used_tib']:,.1f}", f"{r['iops']:,}", r["mode"], r["confidence"])
    con.print(et)
    for r in rows:
        for cpu, bench in r["cpu_models"].items():
            con.print(f"  [{r['cluster']}] {cpu} -> {bench}", markup=False)
    for title, items in (("Transformations", ing.transformations), ("Assumptions", [a.text for a in ing.assumptions]),
                         ("Conflicts resolved", ing.conflicts), ("Warnings", ing.warnings), ("Errors", ing.errors)):
        if items:
            con.print(f"[bold]{title}[/bold] ({len(items)})")
            for i in items[:25]:
                con.print(f"  - {i}", markup=False, highlight=False)
            if len(items) > 25:
                con.print(f"  ... {len(items) - 25} more (see project.json)")
    qs = project.customer_questions_fr if lang == "fr" else project.customer_questions
    con.print("[bold]" + ("Questions pour le client (prêtes à coller)" if lang == "fr" else
                          "Questions for the customer (ready to paste)") + "[/bold]")
    for i, q in enumerate(qs, 1):
        con.print(f"{i}. {q}", markup=False, highlight=False)
