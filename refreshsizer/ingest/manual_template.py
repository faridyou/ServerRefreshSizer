"""RefreshSizer manual template: generator (EN/FR headers) and validating parser."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from refreshsizer.ingest.base import ParseResult
from refreshsizer.ingest.common import find_col, read_workbook, to_float, to_int, to_str
from refreshsizer.model.entities import (
    CRITICALITY,
    HYPERVISORS,
    LICENSING,
    PLATFORM_TYPES,
    WORKLOAD_CLASSES,
    SheetUse,
    SourceHost,
    SourceWorkload,
    StorageArray,
)

HOST_FIELDS = [
    "host_id", "cluster", "site", "vendor", "model", "cpu_model", "sockets", "cores_per_socket",
    "threads_per_core", "nominal_ghz", "ram_gb", "cpu_util_p95_pct", "cpu_util_peak_pct", "cpu_util_avg_pct",
    "mem_used_gb", "smt_mode", "platform_type", "hypervisor", "notes",
]
WORKLOAD_FIELDS = [
    "workload_id", "host_id", "name", "workload_class", "vcpu", "ram_gb_configured", "ram_gb_active",
    "physc_p95", "db_cpu_p95_cores", "storage_used_gib", "storage_provisioned_gib", "iops_p95", "read_pct",
    "throughput_mbps_p95", "latency_target_ms", "licensing", "growth_override_pct", "criticality",
]
STORAGE_FIELDS = ["array", "raw_tib", "used_tib", "drr_observed", "iops_p95", "mbps_p95"]

FR = {
    "host_id": "id_hote", "cluster": "cluster", "site": "site", "vendor": "constructeur", "model": "modele",
    "cpu_model": "modele_cpu", "sockets": "nb_sockets", "cores_per_socket": "coeurs_par_socket",
    "threads_per_core": "threads_par_coeur", "nominal_ghz": "frequence_ghz", "ram_gb": "ram_go",
    "cpu_util_p95_pct": "cpu_p95_pct", "cpu_util_peak_pct": "cpu_pic_pct", "cpu_util_avg_pct": "cpu_moy_pct",
    "mem_used_gb": "memoire_utilisee_go", "smt_mode": "mode_smt", "platform_type": "type_plateforme",
    "hypervisor": "hyperviseur", "notes": "notes",
    "workload_id": "id_charge", "name": "nom", "workload_class": "classe_charge", "vcpu": "vcpu",
    "ram_gb_configured": "ram_go_configuree", "ram_gb_active": "ram_go_active", "physc_p95": "physc_p95",
    "db_cpu_p95_cores": "db_cpu_p95_coeurs", "storage_used_gib": "stockage_utilise_gio",
    "storage_provisioned_gib": "stockage_provisionne_gio", "iops_p95": "iops_p95", "read_pct": "lecture_pct",
    "throughput_mbps_p95": "debit_mbs_p95", "latency_target_ms": "latence_cible_ms", "licensing": "licence",
    "growth_override_pct": "croissance_pct", "criticality": "criticite",
    "array": "baie", "raw_tib": "brut_tio", "used_tib": "utilise_tio", "drr_observed": "drr_observe",
    "mbps_p95": "mbs_p95",
}
SHEET_FR = {"Hosts": "Hotes", "Workloads": "Charges", "Storage": "Stockage", "Policy": "Politique"}
ENUMS = {
    "platform_type": PLATFORM_TYPES, "hypervisor": HYPERVISORS, "workload_class": WORKLOAD_CLASSES,
    "licensing": LICENSING, "criticality": CRITICALITY,
}
REQUIRED_HOST = ("host_id",)
REQUIRED_WL = ("workload_id",)


def _hdr(field: str, lang: str) -> str:
    return FR.get(field, field) if lang == "fr" else field


def generate_template(out: Path, lang: str = "en") -> Path:
    wb = Workbook()
    ws0 = wb.active
    assert ws0 is not None
    ws0.title = "README"
    lines = (
        ["RefreshSizer manual template", "",
         "Fill one row per physical host (Hosts) and per VM/LPAR/database (Workloads).",
         "Utilization in % (0-100). Memory in GB (GiB). Storage in GiB/TiB as labelled.",
         "cpu_model: exact string, e.g. 'Intel Xeon E5-2680 v4' or 'IBM POWER9'.",
         "Policy: key/value overrides (dotted keys allowed, e.g. growth.cpu_pct = 12)."]
        if lang != "fr" else
        ["Modele de saisie RefreshSizer", "",
         "Une ligne par serveur physique (Hotes) et par VM/LPAR/base (Charges).",
         "Utilisation en % (0-100). Memoire en Go (Gio). Stockage en Gio/Tio selon l'en-tete.",
         "modele_cpu : chaine exacte, ex. 'Intel Xeon E5-2680 v4' ou 'IBM POWER9'.",
         "Politique : surcharges cle/valeur (cles pointees, ex. growth.cpu_pct = 12)."]
    )
    for i, line in enumerate(lines, 1):
        ws0.cell(row=i, column=1, value=line)
    bold = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="1F4E79")
    lists_ws = wb.create_sheet("_lists")
    col_idx = 1
    list_refs: dict[str, str] = {}
    for key, values in ENUMS.items():
        for r, v in enumerate(values, 1):
            lists_ws.cell(row=r, column=col_idx, value=v)
        letter = lists_ws.cell(row=1, column=col_idx).column_letter
        list_refs[key] = f"_lists!${letter}$1:${letter}${len(values)}"
        col_idx += 1
    lists_ws.sheet_state = "hidden"
    for sheet, fields in (("Hosts", HOST_FIELDS), ("Workloads", WORKLOAD_FIELDS), ("Storage", STORAGE_FIELDS)):
        ws = wb.create_sheet(SHEET_FR[sheet] if lang == "fr" else sheet)
        for c, f in enumerate(fields, 1):
            cell = ws.cell(row=1, column=c, value=_hdr(f, lang))
            cell.font = bold
            cell.fill = fill
            ws.column_dimensions[cell.column_letter].width = max(12, len(str(cell.value)) + 2)
            if f in list_refs:
                dv = DataValidation(type="list", formula1=f"={list_refs[f]}", allow_blank=True)
                ws.add_data_validation(dv)
                dv.add(f"{cell.column_letter}2:{cell.column_letter}5000")
        ws.freeze_panes = "A2"
    wsp = wb.create_sheet(SHEET_FR["Policy"] if lang == "fr" else "Policy")
    wsp.append(["key", "value"] if lang != "fr" else ["cle", "valeur"])
    wsp.append(["horizon_years", 5])
    wsp.append(["growth.cpu_pct", 15])
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def is_manual_template(sheetnames: list[str]) -> bool:
    low = {s.lower() for s in sheetnames}
    return ("hosts" in low or "hotes" in low) and ("workloads" in low or "charges" in low)


def _sheet(sheets: dict, *names: str):  # type: ignore[no-untyped-def]
    low = {k.lower(): k for k in sheets}
    for n in names:
        if n.lower() in low:
            return low[n.lower()], sheets[low[n.lower()]]
    return None, None


def _cols(df, fields: list[str]) -> dict[str, str | None]:  # type: ignore[no-untyped-def]
    return {f: find_col(df.columns, f, FR.get(f, f), f.replace("_", " ")) for f in fields}


def _enum(value: Any, allowed: list[str], field: str, row_ref: str, res: ParseResult, default: str) -> str:
    s = to_str(value)
    if s is None:
        return default
    for a in allowed:
        if a.lower() == s.lower():
            return a
    res.warnings.append(f"{row_ref}: {field}='{s}' not in {allowed}; using '{default}'")
    return default


def _pct(value: Any, field: str, row_ref: str, res: ParseResult) -> float | None:
    v = to_float(value)
    if v is None:
        return None
    if 0 < v <= 1.0 and "%" not in str(value):
        v *= 100.0
    if v < 0 or v > 100:
        res.errors.append(f"{row_ref}: {field}={v} outside 0-100 %")
        return None
    return v


def parse(path: Path) -> ParseResult:
    path = Path(path)
    res = ParseResult(fmt="manual_template", file=path.name, utilization_source="manual")
    sheets = read_workbook(path)
    used = set()

    name, df = _sheet(sheets, "Hosts", "Hotes")
    if df is not None:
        used.add(name)
        cols = _cols(df, HOST_FIELDS)
        for i, (_, row) in enumerate(df.iterrows(), start=2):
            ref = f"{name}!row {i}"
            g = {k: (row.get(c) if c else None) for k, c in cols.items()}
            hid = to_str(g["host_id"])
            if not hid:
                if any(to_str(v) for v in g.values()):
                    res.errors.append(f"{ref}: host_id is required")
                continue
            sockets = to_int(g["sockets"])
            cps = to_int(g["cores_per_socket"])
            ptype = _enum(g["platform_type"], PLATFORM_TYPES, "platform_type", ref, res, "x86")
            h = SourceHost(
                host_id=hid, cluster=to_str(g["cluster"]) or "default", site=to_str(g["site"]),
                vendor=to_str(g["vendor"]), model=to_str(g["model"]), cpu_model=to_str(g["cpu_model"]),
                sockets=sockets, cores_per_socket=cps, threads_per_core=to_int(g["threads_per_core"]),
                nominal_ghz=to_float(g["nominal_ghz"]), ram_gb=to_float(g["ram_gb"]),
                cpu_util_p95_pct=_pct(g["cpu_util_p95_pct"], "cpu_util_p95_pct", ref, res),
                cpu_util_peak_pct=_pct(g["cpu_util_peak_pct"], "cpu_util_peak_pct", ref, res),
                cpu_util_avg_pct=_pct(g["cpu_util_avg_pct"], "cpu_util_avg_pct", ref, res),
                mem_used_gb=to_float(g["mem_used_gb"]), smt_mode=to_int(g["smt_mode"]), platform_type=ptype,
                hypervisor=_enum(g["hypervisor"], HYPERVISORS, "hypervisor", ref, res, "vSphere"),
                notes=to_str(g["notes"]),
            )
            if h.smt_mode and h.smt_mode not in (1, 2, 4, 8):
                res.errors.append(f"{ref}: smt_mode must be 1/2/4/8")
            if not h.cpu_model:
                res.warnings.append(f"{ref}: cpu_model missing")
            if h.sockets is None or h.cores_per_socket is None:
                res.warnings.append(f"{ref}: sockets/cores_per_socket missing")
            res.hosts.append(h)

    name, df = _sheet(sheets, "Workloads", "Charges")
    if df is not None:
        used.add(name)
        cols = _cols(df, WORKLOAD_FIELDS)
        host_ids = {h.host_id.lower(): h for h in res.hosts}
        for i, (_, row) in enumerate(df.iterrows(), start=2):
            ref = f"{name}!row {i}"
            g = {k: (row.get(c) if c else None) for k, c in cols.items()}
            wid = to_str(g["workload_id"])
            if not wid:
                if any(to_str(v) for v in g.values()):
                    res.errors.append(f"{ref}: workload_id is required")
                continue
            hid = to_str(g["host_id"])
            if hid and hid.lower() not in host_ids:
                res.warnings.append(f"{ref}: host_id '{hid}' not found in Hosts (orphan workload)")
            w = SourceWorkload(
                workload_id=wid, host_id=hid, name=to_str(g["name"]) or wid,
                cluster=host_ids[hid.lower()].cluster if hid and hid.lower() in host_ids else "default",
                workload_class=_enum(g["workload_class"], WORKLOAD_CLASSES, "workload_class", ref, res,
                                     "general_virt"),
                vcpu=to_float(g["vcpu"]), ram_gb_configured=to_float(g["ram_gb_configured"]),
                ram_gb_active=to_float(g["ram_gb_active"]), physc_p95=to_float(g["physc_p95"]),
                db_cpu_p95_cores=to_float(g["db_cpu_p95_cores"]),
                storage_used_gib=to_float(g["storage_used_gib"]),
                storage_provisioned_gib=to_float(g["storage_provisioned_gib"]),
                iops_p95=to_float(g["iops_p95"]), read_pct=_pct(g["read_pct"], "read_pct", ref, res),
                throughput_mbps_p95=to_float(g["throughput_mbps_p95"]),
                latency_target_ms=to_float(g["latency_target_ms"]),
                licensing=_enum(g["licensing"], LICENSING, "licensing", ref, res, "none"),
                growth_override_pct=to_float(g["growth_override_pct"]),
                criticality=_enum(g["criticality"], CRITICALITY, "criticality", ref, res, "tier2"),
            )
            if (w.storage_used_gib is not None and w.storage_provisioned_gib is not None
                    and w.storage_used_gib > w.storage_provisioned_gib * 1.001):
                res.warnings.append(f"{ref}: storage_used_gib > storage_provisioned_gib")
            res.workloads.append(w)

    name, df = _sheet(sheets, "Storage", "Stockage")
    if df is not None:
        used.add(name)
        cols = _cols(df, STORAGE_FIELDS)
        for _, row in df.iterrows():
            g = {k: (row.get(c) if c else None) for k, c in cols.items()}
            arr = to_str(g["array"])
            if not arr:
                continue
            res.storage.append(StorageArray(
                name=arr, raw_tib=to_float(g["raw_tib"]), used_tib=to_float(g["used_tib"]),
                drr_observed=to_float(g["drr_observed"]), iops_p95=to_float(g["iops_p95"]),
                mbps_p95=to_float(g["mbps_p95"])))

    name, df = _sheet(sheets, "Policy", "Politique")
    if df is not None and len(df.columns) >= 2:
        used.add(name)
        kc, vc = df.columns[0], df.columns[1]
        for _, row in df.iterrows():
            k = to_str(row[kc])
            if k:
                res.policy[k] = row[vc]
        from refreshsizer.config.schema import flat_to_nested

        res.policy = flat_to_nested(res.policy)

    for n in sheets:
        res.sheets.append(SheetUse(file=path.name, sheet=n, used=n in used,
                                   classification="manual_template" if n in used else "irrelevant",
                                   reason="template sheet" if n in used else "not a template sheet",
                                   rows=len(sheets[n])))
    return res


def write_validation_report(res: ParseResult, out: Path) -> Path:
    out = Path(out)
    lines = [f"Validation report for {res.file}", "",
             f"Errors (blocking): {len(res.errors)}", *[f"  ERROR: {e}" for e in res.errors], "",
             f"Warnings: {len(res.warnings)}", *[f"  WARN: {w}" for w in res.warnings], ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
