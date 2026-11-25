"""Report outline for the Word (.docx) and PowerPoint (.pptx) exports.

The content of each level (customer / presales / full) is decided here once, as a list of sections made of
simple blocks; docx.py and pptx_report.py only render those blocks. Numbers come from the same report
context as the HTML report (report/build.py), so every format shows the same figures."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from refreshsizer.report.explain import fmt, money, pct


@dataclass
class Block:
    kind: str  # heading | para | kpis | table | image | bullets | pre
    text: str = ""
    level: int = 2
    style: str = ""  # para: lead | muted | note | warn
    items: list[Any] = field(default_factory=list)  # kpis: (value, label); bullets: str
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    align: str = ""  # per column: l / r
    png: bytes | None = None
    numbered: bool = False


@dataclass
class Section:
    title: str
    blocks: list[Block] = field(default_factory=list)
    cluster: str | None = None
    slide: bool = True  # False: shown in the Word document only (PowerPoint puts it on the title slide)


@dataclass
class Outline:
    title: str
    subtitle: str
    level: str
    sections: list[Section] = field(default_factory=list)
    footer: str = ""
    lead: str = ""  # short introduction (PowerPoint title slide)


# ---------------------------------------------------------------------------------------------- pieces
def _current_env(c: dict[str, Any], t: Any) -> list[Block]:
    s = c["src"]
    rows = [[m["cpu"], str(m["hosts"]), str(m["sockets"] or ""), str(m["cores"]), fmt(m["ghz"])] for m in s["models"]]
    rows.append([t("ui_overall"), str(s["hosts"]), str(s["sockets"]), str(s["cores"]),
                 f"{fmt(s['ghz_total'])} GHz {t('c_total')}"])
    util = f"{s['util_pct']} % ({s['util_basis']})" if s["util_pct"] is not None else t("c_no_util")
    facts = [[t("memory"), f"{fmt(s['ram_gb'])} GB"], [t("c_vms"), f"{s['vms']} ({fmt(s['vcpu'])} vCPU)"],
             [t("c_storage_used"), f"{fmt(s['storage_used_tib'])} TiB"], [t("c_cpu_util"), util]]
    if s["collection_days"]:
        facts.append([t("c_collection"), f"{fmt(s['collection_days'])} {t('c_days')}"])
    return [Block("table", header=[t("cpu"), t("hosts"), t("c_sockets"), t("cores"), "GHz"], rows=rows, align="lrrrr"),
            Block("table", header=["", ""], rows=facts, align="lr")]


def _benchmarks(c: dict[str, Any], t: Any) -> list[Block]:
    rows = [[t("c_side_" + b["side"]), str(b["cpu"]), fmt(b.get("value")), str(b.get("spc") or ""),
             str(b.get("system") or ""), str(b.get("url") or "")]
            for b in c["benchmarks"] if b.get("metric") != "rPerf"]
    out = [Block("para", t("c_bench_intro"), style="muted"),
           Block("table", header=["", t("cpu"), "SPECrate2017_int_base", t("c_per_core"), t("system"), t("sources")],
                 rows=rows, align="llrrll")]
    if c["src"]["perf_ratio"]:
        out.append(Block("para", t("c_perf_ratio").replace("{x}", str(c["src"]["perf_ratio"])), style="note"))
    return out


def _proposal(c: dict[str, Any], t: Any, policy: dict[str, Any]) -> list[Block]:
    r, d, s = c["rec"], c["d"], c["src"]
    if not r:
        return [Block("para", "no feasible configuration", style="warn")]
    rows = [[t("c_per_server"), f"{r.config.cpu_count} x {r.config.cpu_name} ({r.config.cores_per_cpu} "
                                f"{t('cores').lower()})"],
            [t("memory"), f"{r.dimm.total_gb} GB ({r.dimm.dimm_count} x {r.dimm.dimm_size_gb} GB)" if r.dimm else ""],
            [t("drives"), f"{r.drives.count} x {r.drives.drive_tb} TB NVMe" if r.drives else t("c_external")],
            [t("network"), r.nics], [t("c_platform"), c["cs"].target_selection.split(";")[0]]]
    if str(policy.get("target_stack", "vmware_vsan")).startswith("nutanix"):
        rows.append([t("cvm_row"), f"{policy.get('nutanix_cvm_vcpu')} vCPU / {policy.get('nutanix_cvm_memory_gb')} GB"])
    sentence = (t("c_assumptions").replace("{g}", fmt(d.growth_cpu_pct)).replace("{y}", str(d.years))
                .replace("{u}", str(round(d.u_max * 100))).replace("{k}", str(r.k_ha)))
    out = [Block("kpis", items=[(str(r.n_total), f"{t('c_servers')} PowerEdge {r.config.platform}"),
                                (str(r.n_total * r.config.total_cores), f"{t('cores')} ({t('c_vs')} {s['cores']})"),
                                (pct(r.util_failed), t("c_util_after_failure"))]),
           Block("table", header=["", ""], rows=rows, align="ll"),
           Block("para", sentence)]
    png = c["png"].get("hosts")
    if png:
        out.append(Block("image", png=png, text=t("c_chart_title")))
    out.append(Block("para", t("c_naive_note"), style="muted"))
    return out


def _alternatives(c: dict[str, Any], t: Any, with_cost: bool) -> list[Block]:
    sc, r = c["cs"].scenarios, c["rec"]
    if len(sc) < 2:
        return []
    header = [t("cpu"), t("c_platform"), t("c_servers").capitalize(), t("cores"), t("c_util_after_failure")]
    header += ["TCO", t("ui_vs_rec"), ""] if with_cost else [t("power") + " (W)", ""]
    rows = []
    for x in sc[:10]:
        row = [f"{x.config.cpu_count} x {x.config.cpu_name}", x.config.platform, str(x.n_total),
               str(x.n_total * x.config.total_cores), pct(x.util_failed)]
        row += [money(x.tco), money(x.tco - r.tco) if r else ""] if with_cost else [f"{x.power_w_total:,.0f}"]
        row.append(", ".join(c["scenario_tags"].get(x.label, [])))
        rows.append(row)
    return [Block("table", header=header, rows=rows, align="llrrr" + ("rrl" if with_cost else "rl"))]


def _summary_table(ctx: dict[str, Any], t: Any) -> Block:
    rows = []
    for c in ctx["clusters"]:
        r, cs = c["rec"], c["cs"]
        rows.append([c["d"].cluster, f"{c['d'].mode.value} / {c['d'].confidence.value}", r.label if r else "-",
                     str(r.n_total) if r else "-", str(cs.uncertainty.p90_hosts) if cs.uncertainty else "-",
                     pct(r.util_failed) if r else "-",
                     f"{cs.naive.core_match_hosts} / {cs.naive.ghz_match_hosts}" if cs.naive else "-",
                     money(c["license_delta"]) if c["license_delta"] is not None else "-",
                     money(r.tco) if r else "-"])
    return Block("table", header=[t("cluster"), t("mode"), t("recommended"), t("hosts"), "P90", t("util_failed"),
                                  t("core_match") + " / GHz", t("licensing_delta"), "TCO"],
                 rows=rows, align="lllrrrrrr")


def _chain(c: dict[str, Any], t: Any) -> Block:
    d, r, s = c["d"], c["rec"], c["src"]
    rows = [[t("p_step_cap"), f"{fmt(d.su_cap_total)} {t('unit')}", f"{s['cores']} cores x SPC {fmt(d.spc_source)}"],
            [t("p_step_d0"), f"{fmt(d.d0)} {t('unit')}", d.mode.value + (f", {s['util_basis']}" if s["util_basis"] else "")],
            [t("p_step_adj"), f"{fmt(d.d0_adj)} {t('unit')}", f"k_arch {fmt(d.k_arch)}, k_virt {fmt(d.k_virt)}"],
            [t("p_step_growth"), f"{fmt(d.d_t)} {t('unit')}", f"{fmt(d.growth_cpu_pct)} %/yr x {d.years} yr"],
            [t("p_step_headroom"), f"{fmt(d.c_req)} {t('unit')}", f"U_max {pct(d.u_max)}"]]
    if r:
        rows += [[t("p_step_host"), f"{fmt(r.su_host_effective)} {t('unit')}",
                  f"{r.config.cpu_count} x {r.config.cpu_name}, SPC {fmt(r.spc_target)}"],
                 [t("p_step_hosts"), str(r.n_total), f"{r.n_total - r.k_ha} + {r.k_ha} HA; {r.n_min_reason}"]]
    rows += [[t("memory"), f"{fmt(d.m_t)} GB", f"{d.mem_basis} {fmt(d.m0)} GB, +{fmt(d.growth_mem_pct)} %/yr"],
             [t("p_step_storage"), f"{fmt(d.storage_raw_tib)} TiB raw",
              f"{fmt(d.storage_used0_tib)} TiB used, +{fmt(d.growth_storage_pct)} %/yr, {d.protection}"]]
    return Block("table", header=["", "", ""], rows=rows, align="lrl")


def _bom(c: dict[str, Any], t: Any, policy: dict[str, Any]) -> list[Block]:
    r = c["rec"]
    if not r:
        return []
    rows = [["Platform", f"Dell PowerEdge {r.config.platform} x {r.n_total}"],
            [t("cpu"), f"{r.config.cpu_count} x {r.config.cpu_name} ({r.config.cores_per_cpu} c)"],
            [t("memory"), f"{r.dimm.dimm_count} x {r.dimm.dimm_size_gb} GB = {r.dimm.total_gb} GB, {r.dimm.dpc} DPC @ "
                          f"{r.dimm.speed_mt_s} MT/s" if r.dimm else ""],
            [t("drives"), f"{r.drives.count} x {r.drives.drive_tb} TB NVMe ({r.drives.raw_tib} TiB)" if r.drives
             else t("c_external")],
            [t("network"), r.nics], [t("psu"), f"2 x {r.config.psu}"]]
    if str(policy.get("target_stack", "vmware_vsan")).startswith("nutanix"):
        rows.append([t("cvm_row"), f"{policy.get('nutanix_cvm_vcpu')} vCPU / {policy.get('nutanix_cvm_memory_gb')} GB "
                                   f"· {c['d'].protection}"])
    out = [Block("table", header=["", ""], rows=rows, align="ll")]
    if c.get("dimm_diagram"):
        out.append(Block("pre", items=list(c["dimm_diagram"]), text=t("dimm_diagram")))
    return out


def _sources(c: dict[str, Any], t: Any) -> list[Block]:
    src = c["src"].get("sources") or []
    if not src:
        return []
    return [Block("para", t("consolidated_from").replace("{n}", str(len(src))) + ": " + ", ".join(src),
                  style="muted")]


def _storage(c: dict[str, Any], t: Any) -> list[Block]:
    st = c.get("storage")
    if not st:
        return []
    out: list[Block] = [Block("para", f, style="warn") for f in st["flags"]]
    if st["notes"]:  # caveats first: a trailing note would otherwise often spill onto a slide of its own
        out.append(Block("para", " · ".join(st["notes"]), style="muted"))
    for title, rows in st["groups"]:
        out += [Block("heading", title, level=3), Block("table", header=["", ""], rows=[list(r) for r in rows],
                                                        align="ll")]
    if st["alternatives"]:
        a = st["alternatives"]
        out += [Block("heading", t("st_alternatives"), level=3),
                Block("table", header=a["header"], rows=a["rows"], align="".join(a["align"]))]
    if st["explanations"]:
        out.append(Block("pre", text=t("formula"), items=[f"{e.title}: {e.formula_text}" for e in st["explanations"]]))
    return out


def _licensing(c: dict[str, Any], t: Any) -> list[Block]:
    r = c["rec"]
    if not r:
        return []
    rows = [[ln.product, f"{fmt(ln.quantity)} {ln.metric}", money(ln.horizon_usd)] for ln in r.licenses]
    rows.append([t("source_side"), "", money(c["src_license_total"])])
    rows.append([t("licensing_delta"), "", money(c["license_delta"])])
    out = [Block("table", header=[t("product"), t("quantity"), t("p_horizon_cost")], rows=rows, align="lrr")]
    if r.oracle_licenses_all:
        out.append(Block("para", f"Oracle: {fmt(r.oracle_licenses_active)} (active nodes) / "
                                 f"{fmt(r.oracle_licenses_all)} (all nodes)."))
    return out


def _checks(c: dict[str, Any], t: Any, all_checks: bool) -> list[Block]:
    items = [f"[{k['status']}] {k['check']}: {k['detail']}" for k in c["constraints"]
             if all_checks or k["status"] != "OK"]
    out = [Block("bullets", items=items)] if items else [Block("para", f"{t('p_all_ok')} ({len(c['constraints'])})")]
    u, d = c["cs"].uncertainty, c["d"]
    if u:
        out.append(Block("para", f"Monte Carlo ({u.runs} runs): P50 {u.p50_hosts} / P90 {u.p90_hosts} "
                                 f"{t('hosts').lower()}."))
    if d.inventory_sensitivity:
        out.append(Block("para", t("sensitivity") + ": " + ", ".join(
            f"{pct(x['assumed_util'])} -> {x.get('hosts', '-')}" for x in d.inventory_sensitivity)))
    return out


def _explanations(c: dict[str, Any]) -> list[Block]:
    out: list[Block] = []
    exps = list(c["d"].explanations) + (list(c["rec"].explanations) if c["rec"] else []) + \
        (list(c["cs"].naive.explanations) if c["cs"].naive else [])
    for e in exps:
        out.append(Block("heading", e.title, level=3))
        out.append(Block("para", e.formula_text))
        if e.inputs:
            out.append(Block("table", header=["input", "value"], rows=[[str(k), fmt(v)] for k, v in e.inputs.items()],
                             align="lr"))
        out.append(Block("para", f"= {fmt(e.output)} {e.unit}"))
        extra = [f"source: {s}" for s in e.source_refs[:4]] + [f"ASSUMPTION: {a}" for a in e.assumptions]
        if extra:
            out.append(Block("bullets", items=extra))
    return out


# ---------------------------------------------------------------------------------------------- levels
def build_outline(ctx: dict[str, Any]) -> Outline:
    ol = _build(ctx)
    for s in ol.sections:  # HTML section numbers ("8. ...") make no sense once sections are regrouped
        s.title = re.sub(r"(:\s*|^)\d+\.\s+", r"\1", s.title)
    return ol


def _build(ctx: dict[str, Any]) -> Outline:
    t, level, project, run, policy = ctx["t"], ctx["level"], ctx["project"], ctx["run"], ctx["policy"]
    clusters = ctx["clusters"]
    if level == "customer":
        ol = Outline(t("c_title"), f"{project.name} · {run.generated_at[:10]}", level, footer=t("c_disclaimer"),
                     lead=t("c_intro"))
        ol.sections.append(Section(t("c_title"), [Block("para", t("c_intro"), style="lead")], slide=False))
        for c in clusters:
            name = c["d"].cluster
            banner = [Block("para", t(c["banner"]), style="warn")] if c["banner"] else []
            ol.sections.append(Section(f"{name}: {t('c_current')}", banner + _sources(c, t) + _current_env(c, t),
                                       name))
            ol.sections.append(Section(f"{name}: {t('c_benchmarks')}", _benchmarks(c, t), name))
            ol.sections.append(Section(f"{name}: {t('c_result')}", _proposal(c, t, policy), name))
            if c.get("storage"):
                ol.sections.append(Section(f"{name}: {t('st_title')}", _storage(c, t), name))
            alt = _alternatives(c, t, with_cost=False)
            if alt:
                ol.sections.append(Section(f"{name}: {t('c_alternatives')}", alt, name))
        steps = ([t("c_next_measure")] if any(c["banner"] for c in clusters) else []) + \
            [t("c_next_pilot"), t("c_next_info"), t("c_next_quote")]
        blocks = [Block("bullets", items=steps, numbered=True)]
        if ctx["questions"]:
            blocks += [Block("heading", t("c_questions"), level=3), Block("bullets", items=list(ctx["questions"]),
                                                                          numbered=True)]
        ol.sections.append(Section(t("c_next"), blocks))
        return ol

    title = t("p_title") if level == "presales" else t("title")
    ol = Outline(title, f"{project.name} · {run.generated_at} · RefreshSizer {ctx['version']}", level,
                 footer=" ".join(run.disclaimers))
    sel = clusters[0]["cs"].target_selection if clusters else ""
    head = [_summary_table(ctx, t), Block("para", f"{t('ui_target_used')}: {sel} · {t('p_prices_note')}", style="muted")]
    head += [Block("para", f"{c['d'].cluster}: {t(c['banner'])}", style="warn") for c in clusters if c["banner"]]
    ol.sections.append(Section(t("s1"), head))
    for c in clusters:
        name, s = c["d"].cluster, c["src"]
        src_txt = (f"{s['hosts']} {t('hosts').lower()}, {s['cores']} {t('cores').lower()} ({fmt(s['ghz_total'])} GHz), "
                   f"{fmt(s['ram_gb'])} GB RAM, {s['vms']} VMs / {fmt(s['vcpu'])} vCPU, "
                   f"{fmt(s['storage_used_tib'])} TiB.")
        if s["util_pct"] is not None:
            src_txt += f" {t('c_cpu_util')}: {s['util_pct']} % ({s['util_basis']})."
        ol.sections.append(Section(f"{name}: {t('p_source')}", _sources(c, t) + [Block("para", src_txt)] + _current_env(c, t)[:1]
                                   + _benchmarks(c, t)[1:], name))
        ol.sections.append(Section(f"{name}: {t('p_chain')}", [_chain(c, t), Block("para", t("unit_def"),
                                                                                    style="muted")], name))
        if c["rec"]:
            ol.sections.append(Section(f"{name}: {t('s8')}", _bom(c, t, policy), name))
            if c.get("storage"):
                ol.sections.append(Section(f"{name}: {t('st_title')}", _storage(c, t), name))
            png = c["png"].get("hosts")
            alt = _alternatives(c, t, with_cost=True)
            if alt or png:
                ol.sections.append(Section(f"{name}: {t('ui_scenarios')}",
                                           ([Block("image", png=png, text=t("host_comparison"))] if png else []) + alt,
                                           name))
            ol.sections.append(Section(f"{name}: {t('s10')}", _licensing(c, t), name))
            ol.sections.append(Section(f"{name}: {t('p_checks')}", _checks(c, t, all_checks=level == "full"), name))
        if level == "full":
            for key in ("pareto", "hist", "tornado"):
                if c["png"].get(key):
                    ol.sections.append(Section(f"{name}: {t({'pareto': 'pareto', 'hist': 'mc_hist', 'tornado': 'tornado'}[key])}",
                                               [Block("image", png=c["png"][key])], name))
            ol.sections.append(Section(f"{name}: {t('s5')}", _explanations(c), name))
    flags = [f"[{c['d'].cluster}] {f}" for c in clusters for f in c["d"].flags[: (None if level == "full" else 6)]]
    warns = project.ingest.warnings + project.ingest.conflicts
    flags += warns if level == "full" else warns[:8]
    q = [Block("bullets", items=flags), Block("heading", t("questions"), level=3),
         Block("bullets", items=list(ctx["questions"]), numbered=True), Block("heading", t("s13"), level=3),
         Block("bullets", items=list(clusters[0]["cs"].recommendations) if clusters else [], numbered=True)]
    ol.sections.append(Section(t("p_quality"), q))
    if level == "full":
        ol.sections.append(Section(t("config_values"), [Block("table", header=["key", t("value")],
                                                              rows=[[k, fmt(v)] for k, v in policy.items()],
                                                              align="ll")]))
    return ol
