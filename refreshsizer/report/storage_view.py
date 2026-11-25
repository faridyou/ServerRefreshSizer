"""Storage design section shared by every report format (HTML/PDF templates, Word/PowerPoint outline, Excel).

Levels: customer -> capacities, layout and connectivity, no prices or formulas; presales -> plus costs and the
alternative array models; full -> plus the formulas (explanations).
"""

from __future__ import annotations

from typing import Any

from refreshsizer.engine.results import StorageDesign
from refreshsizer.report.explain import money


def _tib(v: float) -> str:
    return f"{v:,.1f} TiB"


def storage_view(sd: StorageDesign | None, t: Any, level: str) -> dict[str, Any] | None:
    if sd is None:
        return None
    ext = sd.architecture == "external"
    y = sd.years
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    cap = [
        (t("st_arch"), t("st_arch_external") if ext else t("st_arch_hci")),
        (t("st_solution"), sd.solution),
        (t("st_protection"), (f"PowerStore DRE, {sd.parity} parity" if ext and sd.array else
                              "-" if ext else sd.protection_label)),
        (t("st_used0"), _tib(sd.used0_tib)),
        *([(t("st_inactive"), t("st_inactive_val").replace("{n}", str(sd.inactive_vms))
            .replace("{s}", _tib(sd.inactive_tib)))] if sd.inactive_tib else []),
        (t("st_growth"), f"{sd.growth_pct:g} %/yr x {y} yr -> {_tib(sd.used_t_tib)}"),
        (t("st_drr"), f"{sd.drr:.2f}:1"),
        (t("st_physical").replace("{y}", str(y)), _tib(sd.physical_t_tib)),
    ]
    if sd.usable_tib:
        cap += [(t("st_usable"), f"{_tib(sd.usable_tib)} ({_tib(sd.raw_tib)} raw)"),
                (t("st_effective"), _tib(sd.effective_tib)),
                (t("st_fill").replace("{y}", str(y)),
                 f"{sd.fill_today:.0%} / {sd.fill_t:.0%} (max {sd.max_fill:.0%})")]
    perf = f"{sd.iops0:,.0f} IOPS -> {sd.iops_t:,.0f} IOPS ({sd.read_pct:.0f} % read)"
    if sd.mbps_t:
        perf += f"; {sd.mbps_t:,.0f} MB/s"
    cap.append((t("st_perf").replace("{y}", str(y)), perf))
    groups.append((t("st_g_capacity"), cap))
    lay: list[tuple[str, str]] = []
    if not ext and sd.drives_per_host:
        lay.append((t("st_drives_hci"), f"{sd.drives_per_host} x {sd.drive_tb:g} TB NVMe x {sd.hosts} "
                                        f"{t('c_servers')} = {_tib(sd.raw_tib)} raw"))
    a = sd.array
    if a:
        lay += [(t("st_array"), (f"{a.appliances} x " if a.appliances > 1 else "") + a.model),
                (t("drives"), f"{a.appliances * a.drives_per_appliance} x {a.drive_tb:g} TB NVMe {a.media} "
                              f"({a.drives_per_appliance} {t('st_per_appliance')})"),
                (t("st_enclosures"), t("st_enclosures_val").replace("{n}", str(a.expansions_per_appliance))),
                (t("st_rack_power"), f"{a.rack_units} U, ~{a.power_w:,.0f} W"),
                (t("st_iops_check"), f"{sd.iops_t:,.0f} / {a.indicative_iops:,.0f} = {a.iops_util:.0%} "
                                     f"({t('st_indicative')})")]
    if sd.host_boot:
        lay.append((t("st_boot"), sd.host_boot))
    if lay:
        groups.append((t("st_g_layout"), lay))
    if ext:
        groups.append((t("st_g_connect"), [
            (t("st_protocol"), sd.protocol_label),
            (t("st_host_adapter"), f"{sd.host_adapter} ({sd.host_ports} ports)"),
            (t("st_array_ports"), str(sd.array_ports)),
            (t("st_switch"), t("st_switch_val").replace("{f}", str(sd.fabrics))
             .replace("{p}", str(sd.switch_ports_per_fabric)))]))
        if level != "customer":
            cost = []
            if a:
                cost.append((t("st_capex_array"), money(a.capex_usd)))
            cost += [(t("st_capex_adapters"), money(sd.adapters_capex_usd)),
                     (t("st_capex_total"), money(sd.capex_usd))]
            groups.append((t("st_g_cost"), cost))
    alts: dict[str, Any] | None = None
    if a and sd.alternatives and level != "customer":
        alts = {"header": [t("st_array"), t("drives"), t("st_usable"), t("st_fill_t").replace("{y}", str(y)),
                           "IOPS %", "U", "Capex"],
                "align": ["l", "l", "r", "r", "r", "r", "r"],
                "rows": [[(f"{o.appliances} x " if o.appliances > 1 else "") + o.model,
                          f"{o.appliances * o.drives_per_appliance} x {o.drive_tb:g} TB {o.media}",
                          _tib(o.usable_tib), f"{o.fill_t:.0%}", f"{o.iops_util:.0%}", str(o.rack_units),
                          money(o.capex_usd)] for o in [a] + sd.alternatives]}
    notes = list(sd.notes) if level != "customer" else ([t("st_validate")] if ext else [])
    return {"title": t("st_title"), "external": ext, "groups": groups, "alternatives": alts, "notes": notes,
            "flags": sd.flags, "explanations": sd.explanations if level == "full" else []}
