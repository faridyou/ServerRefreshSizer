"""Report context assembly (shared by HTML, PDF and Excel) and the write_reports entry point."""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from refreshsizer import __version__
from refreshsizer.benchmarks.cpu_matcher import match_cpu
from refreshsizer.benchmarks.spc import resolve_spc
from refreshsizer.catalog.dimm_rules import slot_diagram
from refreshsizer.catalog.loader import load_cpus, load_platforms
from refreshsizer.engine.results import ClusterSizing, SizingRun
from refreshsizer.ingest.universal.summary import estate_summary
from refreshsizer.model.entities import Project, SizingMode
from refreshsizer.report import charts

log = logging.getLogger("refreshsizer.report")


@lru_cache(maxsize=1)
def _strings() -> dict[str, dict[str, str]]:
    with open(Path(__file__).parent / "i18n.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def translator(lang: str) -> Any:
    s = _strings()

    def t(key: str) -> str:
        e = s.get(key)
        if not e:
            return key
        return str(e.get(lang) or e.get("en") or key)

    return t


def benchmark_rows(cs: ClusterSizing) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}

    def add(cpu: str | None, chips: int | None, hint: str | None, side: str) -> None:
        if not cpu:
            return
        r = resolve_spc(cpu, chips=chips, system_hint=hint)
        if not r.results:
            rows[f"{side}-{r.cpu_name}"] = {"side": side, "cpu": r.cpu_name, "metric": "SPC estimate",
                                            "value": "", "cores": "", "spc": round(r.spc, 3),
                                            "system": "", "sponsor": "", "date": "", "url": "", "retrieved": "",
                                            "source": r.method, "note": "; ".join(r.flags)}
        for b in r.results:
            rows[b.id] = {"side": side, "cpu": b.cpu_name, "metric": b.metric, "value": b.value_base,
                          "cores": b.enabled_cores, "chips": b.chips, "spc": round(b.per_core or 0, 3),
                          "system": b.system_name, "sponsor": b.sponsor, "date": b.test_date, "url": b.result_url,
                          "retrieved": b.retrieved_at, "source": b.source, "note": "; ".join(r.flags)}

    d = cs.demand
    for h in d.hosts:
        if h.method != "rperf":
            add(h.cpu_name, h.sockets, None, "source")
    if cs.recommended:
        c = cs.recommended.config
        add(c.cpu_id, c.cpu_count, c.platform, "target")
    for ref in d.source_refs:
        if ref.startswith("IBM rPerf"):
            rows[ref] = {"side": "source", "cpu": ref.split(" - ")[0], "metric": "rPerf", "value": "", "cores": "",
                         "spc": "", "system": "", "sponsor": "IBM", "date": "", "url": ref.split(" - ")[-1],
                         "retrieved": "curated", "source": "curated", "note": ""}
    return list(rows.values())


def constraint_rows(cs: ClusterSizing, t: Any) -> list[dict[str, str]]:
    rec = cs.recommended
    if not rec:
        return []
    d = cs.demand
    out = [{"check": "Failure-mode utilization <= U_max",
            "detail": f"{rec.util_failed:.1%} with {rec.k_ha} host(s) failed vs U_max {d.u_max:.0%}",
            "status": "OK" if rec.util_failed <= d.u_max + 1e-9 or not d.apply_headroom else "FAIL"},
           {"check": "Cluster size", "detail": f"{rec.n_total} hosts ({rec.n_min_reason}); max 64",
            "status": "OK" if rec.n_total <= 64 else "FAIL"}]
    for e in rec.explanations:
        if e.step_id == "numa":
            out.append({"check": "NUMA fit (largest VM)", "detail": e.formula_text,
                        "status": "OK" if e.output else "FAIL"})
        if e.step_id == "vcpu_ratio":
            st = "WARN" if "-> warn" in e.formula_text else ("OK" if "-> ok" in e.formula_text else "FAIL")
            out.append({"check": "vCPU:pCore (raw / performance-normalized)", "detail": e.formula_text,
                        "status": st})
    if rec.dimm:
        out.append({"check": "Memory channels balanced", "detail": f"{rec.dimm.dimm_count} DIMMs, {rec.dimm.dpc} DPC, "
                    f"all {rec.dimm.channels_populated} channels populated", "status": "OK" if rec.dimm.balanced
                    else "FAIL"})
        bw_ok = (rec.dimm.gbs_per_core or 99) >= 4.0 or d.wl_class not in ("hpc", "analytics", "dwh")
        out.append({"check": "Memory bandwidth per core", "detail": f"{rec.dimm.gbs_per_core} GB/s/core",
                    "status": "OK" if bw_ok else "WARN"})
    for w in rec.warnings:
        out.append({"check": "Warning", "detail": w, "status": "WARN"})
    return out


def cluster_context(cs: ClusterSizing, t: Any) -> dict[str, Any]:
    d = cs.demand
    rec = cs.recommended
    ctx: dict[str, Any] = {"cs": cs, "d": d, "rec": rec, "charts": {}, "png": {}}
    if rec and cs.naive:
        png = charts.host_comparison(["recommended", "core match", "GHz match"],
                                     [rec.n_total, cs.naive.core_match_hosts, cs.naive.ghz_match_hosts],
                                     f"{d.cluster}: {t('host_comparison')}")
        ctx["png"]["hosts"] = png
    if cs.pareto:
        allp = list(cs.all_points) or [(c.n_total, c.tco) for c in cs.candidates]
        png = charts.pareto(allp, [(c.n_total, c.tco, c.label) for c in cs.pareto],
                            (rec.n_total, rec.tco) if rec else None, f"{d.cluster}: {t('pareto')}")
        ctx["png"]["pareto"] = png
    if cs.uncertainty:
        u = cs.uncertainty
        ctx["png"]["hist"] = charts.histogram(u.histogram, u.p50_hosts, u.p90_hosts, f"{d.cluster}: {t('mc_hist')}")
        if u.tornado:
            ctx["png"]["tornado"] = charts.tornado(u.tornado, d.c_req, f"{d.cluster}: {t('tornado')}")
    hd = [h for h in d.hosts if h.demand_su is not None]
    if hd:
        ctx["png"]["demand"] = charts.host_demand([h.host_id for h in hd], [h.su_cap for h in hd],
                                                  [h.demand_su or 0 for h in hd], f"{d.cluster}: {t('host_demand')}")
    ctx["charts"] = {k: charts.b64(v) for k, v in ctx["png"].items()}
    ctx["benchmarks"] = benchmark_rows(cs)
    ctx["constraints"] = constraint_rows(cs, t)
    if rec and rec.dimm:
        ctx["dimm_diagram"] = slot_diagram(rec.dimm, load_platforms()[rec.config.platform], rec.config.cpu_count)
    src_total = sum(line.horizon_usd for line in cs.source_licensing)
    ctx["license_delta"] = (rec.license_total - src_total) if rec else None
    ctx["src_license_total"] = src_total
    ctx["banner"] = ("very_low_banner" if d.mode == SizingMode.INVENTORY_ONLY else
                     "low_banner" if d.mode == SizingMode.SNAPSHOT else None)
    cpu = load_cpus().get(rec.config.cpu_id) if rec else None
    ctx["cpu"] = cpu
    ctx["platform"] = load_platforms().get(rec.config.platform) if rec else None
    src_cpus = {}
    for h in d.hosts:
        m = match_cpu(h.cpu_name)
        src_cpus[h.cpu_name] = m.name if m else h.cpu_name
    ctx["src_cpus"] = src_cpus
    return ctx


def build_context(project: Project, lang: str = "en") -> dict[str, Any]:
    assert project.sizing is not None
    run = SizingRun.model_validate(project.sizing)
    t = translator(lang)
    clusters = [cluster_context(cs, t) for cs in run.clusters]
    return {"project": project, "run": run, "t": t, "lang": lang, "clusters": clusters,
            "estate": estate_summary(project), "version": __version__,
            "questions": project.customer_questions_fr if lang == "fr" else project.customer_questions,
            "policy": run.policy}


def write_reports(project: Project, out: Path, formats: list[str], lang: str = "en",
                  progress: Callable[[float, str], None] | None = None) -> list[Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(0.05, "charts and report context")
    ctx = build_context(project, lang)
    stem = "refreshsizer_report" + ("_fr" if lang == "fr" else "")
    written: list[Path] = []
    if "html" in formats:
        from refreshsizer.report.html import render_html

        p = out / f"{stem}.html"
        p.write_text(render_html(ctx, pdf=False), encoding="utf-8")
        written.append(p)
    if "xlsx" in formats:
        from refreshsizer.report.excel import write_excel

        if progress:
            progress(0.4, "Excel workbook (live formulas)")
        written.append(write_excel(ctx, out / f"{stem}.xlsx"))
    if "pdf" in formats:
        from refreshsizer.report.pdf import write_pdf

        if progress:
            progress(0.6, "PDF")
        p2 = write_pdf(ctx, out / f"{stem}.pdf")
        if p2:
            written.append(p2)
        elif "html" not in formats:
            from refreshsizer.report.html import render_html

            p = out / f"{stem}.html"
            p.write_text(render_html(ctx, pdf=False), encoding="utf-8")
            written.append(p)
    return written
