"""Excel report: one sheet per section, with LIVE formulas so the customer can audit and change inputs.

Every formula is written with the engine's value as cached result (shown before recalculation)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import xlsxwriter
from xlsxwriter.utility import xl_rowcol_to_cell

from refreshsizer.engine.results import ClusterSizing


class Book:
    def __init__(self, path: Path):
        self.wb = xlsxwriter.Workbook(str(path))
        self.h = self.wb.add_format({"bold": True, "bg_color": "#1F4E79", "font_color": "#FFFFFF", "border": 1})
        self.b = self.wb.add_format({"bold": True})
        self.title = self.wb.add_format({"bold": True, "font_size": 14, "font_color": "#1F4E79"})
        self.inp = self.wb.add_format({"bg_color": "#FFF2CC", "border": 1, "num_format": "#,##0.0###"})
        self.f = self.wb.add_format({"bg_color": "#E2EFDA", "border": 1, "num_format": "#,##0.00"})
        self.fi = self.wb.add_format({"bg_color": "#E2EFDA", "border": 1, "num_format": "0"})
        self.pct = self.wb.add_format({"bg_color": "#E2EFDA", "border": 1, "num_format": "0.0%"})
        self.money = self.wb.add_format({"bg_color": "#E2EFDA", "border": 1, "num_format": "$#,##0"})
        self.minp = self.wb.add_format({"bg_color": "#FFF2CC", "border": 1, "num_format": "$#,##0.00"})
        self.wrap = self.wb.add_format({"text_wrap": True, "valign": "top"})
        self.muted = self.wb.add_format({"font_color": "#52514E", "italic": True})

    def sheet(self, name: str, title: str, widths: dict[int, int] | None = None) -> Any:
        ws = self.wb.add_worksheet(name[:31])
        ws.write(0, 0, title, self.title)
        ws.write(1, 0, "Yellow = editable input; green = live formula (cached value = RefreshSizer result).",
                 self.muted)
        for c, w in (widths or {0: 38, 1: 16, 2: 70}).items():
            ws.set_column(c, c, w)
        return ws

    def header(self, ws: Any, row: int, cols: list[str]) -> None:
        for j, c in enumerate(cols):
            ws.write(row, j, c, self.h)


def _abs(sheet: str, row: int, col: int) -> str:
    return f"'{sheet}'!{xl_rowcol_to_cell(row, col, row_abs=True, col_abs=True)}"


def write_excel(ctx: dict[str, Any], out: Path) -> Path:
    bk = Book(Path(out))
    t = ctx["t"]
    run = ctx["run"]
    project = ctx["project"]
    clusters: list[dict[str, Any]] = ctx["clusters"]
    refs: dict[str, dict[str, str]] = {}

    S1, S2, S3, S4, S5, S6, S7, S8, S9, S10, S11, S12, S13, S14 = (
        "1 Summary", "2 Inputs & confidence", "3 Source hosts", "4 Benchmarks", "5 Normalization",
        "6 Growth headroom HA", "7 Candidates", "8 BoM", "9 Memory storage net", "10 Licensing", "11 Constraints",
        "12 Uncertainty", "13 Validation", "14 Appendix")
    ws1 = bk.sheet(S1, t("s1"), {0: 26, 1: 30, 2: 14, 3: 14, 4: 14, 5: 14, 6: 14, 7: 16, 8: 16})
    ws2 = bk.sheet(S2, t("s2"), {0: 30, 1: 26, 2: 12, 3: 20, 4: 8, 5: 70})
    ws3 = bk.sheet(S3, t("s3"), {0: 22, 1: 30, 2: 8, 3: 10, 4: 12, 5: 10, 6: 12, 7: 12, 8: 14})
    ws4 = bk.sheet(S4, t("s4"), {0: 8, 1: 26, 2: 24, 3: 10, 4: 8, 5: 8, 6: 32, 7: 16, 8: 12, 9: 50, 10: 12})
    ws5 = bk.sheet(S5, t("s5"))
    ws6 = bk.sheet(S6, t("s6"))
    ws7 = bk.sheet(S7, t("s7"), {0: 4, 1: 34, 2: 10, 3: 10, 4: 8, 5: 10, 6: 10, 7: 16, 8: 10, 9: 6, 10: 14, 11: 14, 12: 14})
    ws8 = bk.sheet(S8, t("s8"), {0: 22, 1: 90})
    ws9 = bk.sheet(S9, t("s9"))
    ws10 = bk.sheet(S10, t("s10"), {0: 30, 1: 34, 2: 10, 3: 12, 4: 14, 5: 14, 6: 8, 7: 16, 8: 70})
    ws11 = bk.sheet(S11, t("s11"), {0: 40, 1: 90, 2: 10})
    ws12 = bk.sheet(S12, t("s12"), {0: 22, 1: 12, 2: 12, 3: 12, 4: 12, 5: 12, 6: 12})
    ws13 = bk.sheet(S13, t("s13"), {0: 120})
    ws14 = bk.sheet(S14, t("s14"), {0: 34, 1: 60})

    r3, r5, r6, r9, r10 = 3, 3, 3, 3, 3
    for c in clusters:
        cs: ClusterSizing = c["cs"]
        d = cs.demand
        rec = cs.recommended
        R: dict[str, str] = {}
        refs[d.cluster] = R

        # ---- 3 Source hosts (live per-host demand where host utilization drives D0) -----------------------
        ws3.write(r3, 0, d.cluster, bk.b)
        r3 += 1
        bk.header(ws3, r3, ["host", "CPU", "cores", "SPC", "SU_cap", "U used %", "saturation k", "SU demand",
                            "basis"])
        r3 += 1
        first = r3
        host_path = any(k in d.d0_by_path for k in ("host_summary", "snapshot")) and any(
            h.util_used_pct is not None for h in d.hosts)
        sat_thr = float(run.policy.get("saturation_threshold", 0.8)) * 100
        sat_up = float(run.policy.get("saturation_uplift", 1.1))
        for h in d.hosts:
            ws3.write(r3, 0, h.host_id)
            ws3.write(r3, 1, h.cpu_name)
            ws3.write_number(r3, 2, h.cores, bk.inp)
            ws3.write_number(r3, 3, h.spc, bk.inp)
            ws3.write_formula(r3, 4, f"=C{r3 + 1}*D{r3 + 1}", bk.f, h.su_cap)
            if h.util_used_pct is not None:
                ws3.write_number(r3, 5, h.util_used_pct, bk.inp)
                sat = sat_up if h.util_used_pct > sat_thr else 1.0
                ws3.write_formula(r3, 6, f"=IF(F{r3 + 1}>{sat_thr},{sat_up},1)", bk.f, sat)
                ws3.write_formula(r3, 7, f"=F{r3 + 1}/100*E{r3 + 1}*G{r3 + 1}", bk.f, h.demand_su or 0)
                ws3.write(r3, 8, h.util_basis or "")
            r3 += 1
        last = r3 - 1
        R["su_cap"] = f"SUM('{S3}'!E{first + 1}:E{last + 1})" if d.hosts else str(d.su_cap_total)
        r3 += 2

        # ---- 5 Normalization -------------------------------------------------------------------------------
        def row(ws: Any, sheet: str, rr: int, key: str, label: str, value: Any, formula: str | None = None,
                note: str = "", fmt: Any = None) -> int:
            ws.write(rr, 0, label)
            if formula:
                ws.write_formula(rr, 1, formula, fmt or bk.f, value)
            elif isinstance(value, (int, float)):
                ws.write_number(rr, 1, float(value), bk.inp)
            else:
                ws.write(rr, 1, value)
            ws.write(rr, 2, note, bk.wrap)
            R[key] = _abs(sheet, rr, 1)  # noqa: B023 - called within the same iteration
            return rr + 1

        ws5.write(r5, 0, f"{d.cluster} - {d.mode.value} ({d.confidence.value})", bk.b)
        r5 += 1
        share_note = ""
        r5 = row(ws5, S5, r5, "su_cap_total", "Source capacity SU_cap (sum)", d.su_cap_total,
                 f"={R['su_cap']}" if d.hosts else None, "SPC x cores per host (sheet 3)")
        if host_path:
            share = d.d0 / max(sum(h.demand_su or 0 for h in d.hosts), 1e-9)
            r5 = row(ws5, S5, r5, "share", "Share of host demand (DB split)", round(share, 6), None,
                     "1.0 unless per-core-licensed workloads were split into their own cluster")
            r5 = row(ws5, S5, r5, "d0", "D0 demand (SU)", d.d0,
                     f"=SUM('{S3}'!H{first + 1}:H{last + 1})*{R['share']}", "sum of host U x SU_cap (sheet 3)")
        else:
            share_note = f"computed by the engine via path {', '.join(d.d0_by_path)} (see sheet 5 notes / report)"
            r5 = row(ws5, S5, r5, "d0", "D0 demand (SU)", d.d0, None, share_note)
        r5 = row(ws5, S5, r5, "k_arch", "k_arch", d.k_arch)
        r5 = row(ws5, S5, r5, "k_virt", "k_virt", d.k_virt)
        r5 = row(ws5, S5, r5, "d0_adj", "D0' = D0 x k_arch x k_virt", d.d0_adj,
                 f"={R['d0']}*{R['k_arch']}*{R['k_virt']}")
        for e in d.explanations:
            if e.step_id in ("src_capacity", "d0", "adjust"):
                ws5.write(r5, 0, e.title, bk.muted)
                ws5.write(r5, 2, e.formula_text, bk.wrap)
                r5 += 1
        r5 += 1

        # ---- 6 Growth / headroom / HA / naive ----------------------------------------------------------------
        ws6.write(r6, 0, d.cluster, bk.b)
        r6 += 1
        events = d.d_t - d.d0_adj * (1 + d.growth_cpu_pct / 100) ** d.years
        r6 = row(ws6, S6, r6, "g", "CPU growth %/yr", d.growth_cpu_pct)
        r6 = row(ws6, S6, r6, "T", "Horizon (years)", d.years)
        r6 = row(ws6, S6, r6, "ev", "Step events (SU)", round(events, 6))
        r6 = row(ws6, S6, r6, "d_t", "D_T = D0' x (1+g)^T + events", d.d_t,
                 f"={R['d0_adj']}*(1+{R['g']}/100)^{R['T']}+{R['ev']}")
        r6 = row(ws6, S6, r6, "umax", "U_max", d.u_max)
        r6 = row(ws6, S6, r6, "apply_h", "Apply headroom (1/0)", 1 if d.apply_headroom else 0)
        r6 = row(ws6, S6, r6, "c_req", "C_req = D_T / U_max", d.c_req,
                 f"=IF({R['apply_h']}=1,{R['d_t']}/{R['umax']},{R['d_t']})")
        if rec:
            r6 = row(ws6, S6, r6, "spc_t", "Target SPC", rec.spc_target, None, rec.label)
            r6 = row(ws6, S6, r6, "cores_t", "Target cores per CPU", rec.config.cores_per_cpu)
            r6 = row(ws6, S6, r6, "cpus_t", "CPUs per host", rec.config.cpu_count)
            r6 = row(ws6, S6, r6, "su_red", "vSAN SU reserve factor", round(rec.su_host_effective / rec.su_host, 6))
            r6 = row(ws6, S6, r6, "su_host", "SU per host (effective)", rec.su_host_effective,
                     f"={R['spc_t']}*{R['cores_t']}*{R['cpus_t']}*{R['su_red']}")
            r6 = row(ws6, S6, r6, "n_act", "N_active (CPU) = CEILING(C_req/SU_host)", rec.n_total - rec.k_ha,
                     f"=CEILING({R['c_req']}/{R['su_host']}-1E-9,1)", fmt=bk.fi)
            r6 = row(ws6, S6, r6, "k_ha", "k_HA (N+1, N+2 above 16 hosts)", rec.k_ha,
                     f"=IF({R['n_act']}+1>16,2,1)", fmt=bk.fi)
            r6 = row(ws6, S6, r6, "n_min", "Minimum hosts (protection / HA)", int(
                ''.join(ch for ch in rec.n_min_reason.split('hosts')[0] if ch.isdigit()) or 2), None, rec.n_min_reason)
            r6 = row(ws6, S6, r6, "mem_host", "Installed memory per host (GB)", rec.dimm.total_gb if rec.dimm else 0)
            r6 = row(ws6, S6, r6, "drv_host", "Installed raw TiB per host", rec.drives.raw_tib if rec.drives else 0)
            r6 = row(ws6, S6, r6, "n_mem", "Hosts for memory = CEILING(M_T/mem)+k_HA", rec.n_total,
                     f"=IF({R['mem_host']}>0,CEILING(M_T_REF/{R['mem_host']}-1E-9,1)+{R['k_ha']},0)", fmt=bk.fi)
            n_mem_row = r6 - 1
            r6 = row(ws6, S6, r6, "n_sto", "Hosts for storage = CEILING(Raw/TiB per host)", 0,
                     f"=IF({R['drv_host']}>0,CEILING(RAW_REF/{R['drv_host']}-1E-9,1),0)", fmt=bk.fi)
            n_sto_row = r6 - 1
            r6 = row(ws6, S6, r6, "n_total", "N_total = MAX(CPU+HA, minimum, memory, storage)", rec.n_total,
                     f"=MAX({R['n_act']}+{R['k_ha']},{R['n_min']},{R['n_mem']},{R['n_sto']})", fmt=bk.fi,
                     note="engine also enforces vCPU:pCore and constraint loops; see sheet 11")
            r6 = row(ws6, S6, r6, "u_fail", "Utilization with k_HA hosts failed", rec.util_failed,
                     f"={R['d_t']}/(({R['n_total']}-{R['k_ha']})*{R['su_host']})", fmt=bk.pct)
            r6 = row(ws6, S6, r6, "src_cores", "Source cores", d.source_cores)
            r6 = row(ws6, S6, r6, "ghz_used", "Source GHz used (cores x GHz x U)", d.source_ghz_used)
            r6 = row(ws6, S6, r6, "base_ghz", "Target base GHz", ctx_cpu_ghz(c))
            core_f = (f"=CEILING({R['src_cores']}*(1+{R['g']}/100)^{R['T']}/{R['umax']}/({R['cores_t']}*{R['cpus_t']})"
                      f"-1E-9,1)")
            nc = cs.naive.core_match_hosts if cs.naive else 0
            ng = cs.naive.ghz_match_hosts if cs.naive else 0
            r6 = row(ws6, S6, r6, "core_act", "Core match active hosts", nc - (2 if nc > 17 else 1), core_f, fmt=bk.fi)
            r6 = row(ws6, S6, r6, "core_match", "Naive core match (+HA)",
                     cs.naive.core_match_hosts if cs.naive else 0,
                     f"={R['core_act']}+IF({R['core_act']}+1>16,2,1)", fmt=bk.fi)
            ghz_f = (f"=CEILING({R['ghz_used']}*(1+{R['g']}/100)^{R['T']}/{R['umax']}/({R['cores_t']}*{R['cpus_t']}*"
                     f"{R['base_ghz']})-1E-9,1)")
            r6 = row(ws6, S6, r6, "ghz_act", "GHz match active hosts", ng - (2 if ng > 17 else 1), ghz_f, fmt=bk.fi)
            r6 = row(ws6, S6, r6, "ghz_match", "Naive GHz match (+HA)", cs.naive.ghz_match_hosts if cs.naive else 0,
                     f"={R['ghz_act']}+IF({R['ghz_act']}+1>16,2,1)", fmt=bk.fi)
        r6 += 1

        # ---- 9 Memory / storage / network -------------------------------------------------------------------
        ws9.write(r9, 0, d.cluster, bk.b)
        r9 += 1
        mevents = 0.0
        r9 = row(ws9, S9, r9, "m0", f"M0 memory basis ({d.mem_basis}) GB", d.m0)
        r9 = row(ws9, S9, r9, "gm", "Memory growth %/yr", d.growth_mem_pct)
        r9 = row(ws9, S9, r9, "oh", "Hypervisor overhead", float(run.policy.get("hypervisor_mem_overhead", 0.05)))
        r9 = row(ws9, S9, r9, "rf", "Free reserve", float(run.policy.get("mem_free_reserve", 0.05)))
        r9 = row(ws9, S9, r9, "mev", "Memory step events GB", mevents)
        r9 = row(ws9, S9, r9, "m_t", "M_T = (M0 (1+g)^T + events)(1+o)/(1-r)", d.m_t,
                 f"=({R['m0']}*(1+{R['gm']}/100)^{R['T']}+{R['mev']})*(1+{R['oh']})/(1-{R['rf']})")
        r9 = row(ws9, S9, r9, "used0", "Used storage TiB (basis)", d.storage_used0_tib)
        r9 = row(ws9, S9, r9, "gs", "Storage growth %/yr", d.growth_storage_pct)
        r9 = row(ws9, S9, r9, "drr", "Data reduction ratio (effective)", d.drr)
        r9 = row(ws9, S9, r9, "pp", f"Protection overhead ({d.protection})", d.p_prot)
        r9 = row(ws9, S9, r9, "slack", "Slack", float(run.policy.get("slack", 0.3)))
        r9 = row(ws9, S9, r9, "raw", "Raw TiB = Used0 (1+g)^T / DRR x P / (1-slack)", d.storage_raw_tib,
                 f"={R['used0']}*(1+{R['gs']}/100)^{R['T']}/{R['drr']}*{R['pp']}/(1-{R['slack']})")
        r9 = row(ws9, S9, r9, "iops", "Back-end IOPS at horizon", d.backend_iops_t)
        r9 = row(ws9, S9, r9, "mbps", "Peak throughput MB/s at horizon", d.mbps_t)
        if rec:
            ws9.write(r9, 0, "NICs per host")
            ws9.write(r9, 1, rec.nics)
            r9 += 1
        r9 += 1
        if rec:
            # patch memory / storage host-count formulas now that M_T and Raw cells exist
            mf = f"=IF({R['mem_host']}>0,CEILING({R['m_t']}/{R['mem_host']}-1E-9,1)+{R['k_ha']},0)"
            n_mem_val = (int(-(-d.m_t // rec.dimm.total_gb)) + rec.k_ha) if rec.dimm else 0
            ws6.write_formula(n_mem_row, 1, mf, bk.fi, n_mem_val)
            sf = f"=IF({R['drv_host']}>0,CEILING({R['raw']}/{R['drv_host']}-1E-9,1),0)"
            n_sto_val = int(-(-d.storage_raw_tib // rec.drives.raw_tib)) if rec.drives and rec.drives.raw_tib else 0
            ws6.write_formula(n_sto_row, 1, sf, bk.fi, n_sto_val)

        # ---- 10 Licensing ---------------------------------------------------------------------------------
        ws10.write(r10, 0, d.cluster, bk.b)
        r10 += 1
        bk.header(ws10, r10, ["side", "product", "quantity", "unit price", "one-time", "annual", "years",
                              "horizon", "note"])
        r10 += 1
        tgt_rows: list[int] = []
        src_rows: list[int] = []
        lines = [("target", x) for x in (rec.licenses if rec else [])] + [("source", x) for x in cs.source_licensing]
        for side, ln in lines:
            rr = r10
            ws10.write(rr, 0, side)
            ws10.write(rr, 1, ln.product)
            ws10.write_number(rr, 2, ln.quantity, bk.inp)
            ws10.write_number(rr, 3, ln.unit_price, bk.minp)
            if ln.one_time_usd:
                ws10.write_formula(rr, 4, f"=C{rr + 1}*D{rr + 1}", bk.money, ln.one_time_usd)
                ratio = ln.annual_usd / ln.one_time_usd if ln.one_time_usd else 0
                ws10.write_formula(rr, 5, f"=E{rr + 1}*{ratio:.6g}", bk.money, ln.annual_usd)
            else:
                ws10.write_number(rr, 4, 0, bk.money)
                if ln.unit_price:
                    ws10.write_formula(rr, 5, f"=C{rr + 1}*D{rr + 1}", bk.money, ln.annual_usd)
                else:
                    ws10.write_number(rr, 5, ln.annual_usd, bk.money)
            ws10.write_number(rr, 6, d.years, bk.inp)
            ws10.write_formula(rr, 7, f"=E{rr + 1}+F{rr + 1}*G{rr + 1}", bk.money, ln.horizon_usd)
            ws10.write(rr, 8, ln.note, bk.wrap)
            (tgt_rows if side == "target" else src_rows).append(rr)
            r10 += 1
        tsum = "+".join(f"H{x + 1}" for x in tgt_rows) or "0"
        ssum = "+".join(f"H{x + 1}" for x in src_rows) or "0"
        ws10.write(r10, 1, "Target total", bk.b)
        ws10.write_formula(r10, 7, f"={tsum}", bk.money, rec.license_total if rec else 0)
        R["lic_t"] = _abs(S10, r10, 7)
        r10 += 1
        ws10.write(r10, 1, "Source total", bk.b)
        ws10.write_formula(r10, 7, f"={ssum}", bk.money, c["src_license_total"])
        R["lic_s"] = _abs(S10, r10, 7)
        r10 += 1
        ws10.write(r10, 1, "Delta (target - source)", bk.b)
        ws10.write_formula(r10, 7, f"={R['lic_t']}-{R['lic_s']}", bk.money, c["license_delta"] or 0)
        R["lic_d"] = _abs(S10, r10, 7)
        r10 += 3

    # ---- 1 Summary ---------------------------------------------------------------------------------------
    bk.header(ws1, 3, [t("cluster"), t("recommended"), t("mode"), t("confidence"), "C_req SU", t("hosts"),
                       t("util_failed"), t("core_match"), t("ghz_match"), "P90", t("licensing_delta"), t("tco")])
    for i, c in enumerate(clusters):
        rr = 4 + i
        cs = c["cs"]
        d = cs.demand
        rec = cs.recommended
        R = refs[d.cluster]
        ws1.write(rr, 0, d.cluster)
        ws1.write(rr, 1, rec.label if rec else "none feasible")
        ws1.write(rr, 2, d.mode.value)
        ws1.write(rr, 3, d.confidence.value)
        ws1.write_formula(rr, 4, f"={R['c_req']}", bk.f, d.c_req)
        if rec:
            ws1.write_formula(rr, 5, f"={R['n_total']}", bk.fi, rec.n_total)
            ws1.write_formula(rr, 6, f"={R['u_fail']}", bk.pct, rec.util_failed)
            ws1.write_formula(rr, 7, f"={R['core_match']}", bk.fi, cs.naive.core_match_hosts if cs.naive else 0)
            ws1.write_formula(rr, 8, f"={R['ghz_match']}", bk.fi, cs.naive.ghz_match_hosts if cs.naive else 0)
            ws1.write_number(rr, 9, cs.uncertainty.p90_hosts if cs.uncertainty else 0)
            ws1.write_formula(rr, 10, f"={R['lic_d']}", bk.money, c["license_delta"] or 0)
            ws1.write_number(rr, 11, rec.tco, bk.money)
    img_row = 6 + len(clusters)
    for c in clusters:
        for key in ("hosts", "pareto"):
            png = c["png"].get(key)
            if png:
                ws1.insert_image(img_row, 0, f"{key}.png", {"image_data": io.BytesIO(png), "x_scale": 0.6,
                                                            "y_scale": 0.6})
                img_row += 18
    for i, w in enumerate(run.warnings):
        ws1.write(img_row + i, 0, w, bk.muted)

    # ---- 2 Inputs & confidence ---------------------------------------------------------------------------
    r = 3
    bk.header(ws2, r, ["file", "sheet", "used", "class", "rows", "reason"])
    r += 1
    for s in project.ingest.sheets:
        for j, v in enumerate([s.file, s.sheet, "yes" if s.used else "no", s.classification, s.rows, s.reason]):
            ws2.write(r, j, v)
        r += 1
    r += 1
    bk.header(ws2, r, ["sheet", "column", "field", "unit", "conf.", "method / sample"])
    r += 1
    for m in project.ingest.mappings:
        for j, v in enumerate([m.sheet, m.column, m.target or "-", m.unit or "", m.confidence,
                               f"{m.method}: {m.sample or ''} -> {m.transformed or ''}"]):
            ws2.write(r, j, v)
        r += 1
    r += 1
    ws2.write(r, 0, t("assumptions"), bk.b)
    r += 1
    for a in project.ingest.assumptions:
        ws2.write(r, 0, a.category)
        ws2.write(r, 1, a.text)
        r += 1
    for c in clusters:
        for f in c["d"].flags:
            ws2.write(r, 0, c["d"].cluster)
            ws2.write(r, 1, f)
            r += 1
    r += 1
    ws2.write(r, 0, t("questions"), bk.b)
    r += 1
    for q in ctx["questions"]:
        ws2.write(r, 0, q)
        r += 1

    # ---- 4 Benchmarks ---------------------------------------------------------------------------------
    r = 3
    ws4.write(2, 0, t("base_vs_peak"), bk.muted)
    bk.header(ws4, r, ["side", "CPU", "metric", "value", "cores", "SPC", "system", "sponsor", "date", "URL",
                       "source"])
    r += 1
    for c in clusters:
        for b in c["benchmarks"]:
            for j, k in enumerate(["side", "cpu", "metric", "value", "cores", "spc", "system", "sponsor", "date",
                                   "url", "source"]):
                v = b.get(k)
                ws4.write(r, j, "" if v is None else v)
            r += 1
    for dsc in run.disclaimers:
        r += 1
        ws4.write(r, 0, dsc, bk.muted)

    # ---- 7 Candidates -----------------------------------------------------------------------------------
    r = 3
    for c in clusters:
        ws7.write(r, 1, c["d"].cluster, bk.b)
        r += 1
        bk.header(ws7, r, ["#", "config", "SPC", "SU/host", "hosts", "util failed", "RAM/host GB", "drives",
                           "power W", "RU", "capex", "licenses", "TCO"])
        r += 1
        for i, x in enumerate(c["cs"].candidates, 1):
            vals = [i, x.label, x.spc_target, x.su_host, x.n_total, x.util_failed,
                    x.dimm.total_gb if x.dimm else 0,
                    f"{x.drives.count} x {x.drives.drive_tb} TB" if x.drives else "external", x.power_w_total,
                    x.rack_units, x.capex_total, x.license_total]
            for j, v in enumerate(vals):
                ws7.write(r, j, v)
            ws7.write_formula(r, 12, f"=K{r + 1}+L{r + 1}+{x.power_opex}", bk.money, x.tco)
            r += 1
        r += 2

    # ---- 7b CPU scenarios (best configuration per CPU model) ------------------------------------------
    for c in clusters:
        cs = c["cs"]
        if not cs.scenarios:
            continue
        ws7.write(r, 1, f"{c['d'].cluster}: {t('ui_scenarios')} ({cs.target_selection})", bk.b)
        r += 1
        bk.header(ws7, r, ["#", "CPU scenario", "SPEC basis", "SPC", "hosts", "util failed", "cores", "", "power W",
                           "RU", "capex", "licenses", "TCO", "vs recommended", "tags"])
        r += 1
        first_row = r
        rec_row = None
        for i, x in enumerate(cs.scenarios, 1):
            tags = c["scenario_tags"].get(x.label, [])
            if "recommended" in tags:
                rec_row = r
            vals = [i, x.label, c["scenario_basis"][x.label], x.spc_target, x.n_total, x.util_failed,
                    x.n_total * x.config.total_cores, "", x.power_w_total, x.rack_units, x.capex_total,
                    x.license_total]
            for j, v in enumerate(vals):
                ws7.write(r, j, v)
            ws7.write_formula(r, 12, f"=K{r + 1}+L{r + 1}+{x.power_opex}", bk.money, x.tco)
            ws7.write(r, 14, ", ".join(tags))
            r += 1
        ref = rec_row if rec_row is not None else first_row
        base = cs.recommended.tco if cs.recommended else 0
        for idx, x in enumerate(cs.scenarios):
            srow = first_row + idx
            ws7.write_formula(srow, 13, f"=M{srow + 1}-$M${ref + 1}", bk.money, x.tco - base)
        r += 2

    # ---- 8 BoM -------------------------------------------------------------------------------------------
    r = 3
    for c in clusters:
        rec = c["rec"]
        if not rec:
            continue
        ws8.write(r, 0, c["d"].cluster, bk.b)
        r += 1
        items = [("Platform", f"Dell PowerEdge {rec.config.platform} x {rec.n_total}"),
                 ("CPU", f"{rec.config.cpu_count} x {rec.config.cpu_name}"),
                 ("Memory", f"{rec.dimm.dimm_count} x {rec.dimm.dimm_size_gb} GB = {rec.dimm.total_gb} GB, "
                            f"{rec.dimm.dpc} DPC @ {rec.dimm.speed_mt_s} MT/s" if rec.dimm else "-"),
                 ("Drives", f"{rec.drives.count} x {rec.drives.drive_tb} TB NVMe" if rec.drives else "external"),
                 ("NICs", rec.nics), ("PSU", f"2 x {rec.config.psu}")]
        for k, v in items:
            ws8.write(r, 0, k)
            ws8.write(r, 1, v)
            r += 1
        for line in c.get("dimm_diagram", []):
            ws8.write(r, 1, line)
            r += 1
        r += 1

    # ---- 11 Constraints, 12 Uncertainty, 13 Validation -------------------------------------------------
    r = 3
    for c in clusters:
        ws11.write(r, 0, c["d"].cluster, bk.b)
        r += 1
        for k in c["constraints"]:
            ws11.write(r, 0, k["check"])
            ws11.write(r, 1, k["detail"], bk.wrap)
            ws11.write(r, 2, k["status"])
            r += 1
        r += 1
    r = 3
    for c in clusters:
        u = c["cs"].uncertainty
        ws12.write(r, 0, c["d"].cluster, bk.b)
        r += 1
        if u:
            ws12.write(r, 0, "runs / seed")
            ws12.write(r, 1, u.runs)
            ws12.write(r, 2, u.seed)
            r += 1
            ws12.write(r, 0, "P50 / P90 hosts")
            ws12.write(r, 1, u.p50_hosts)
            ws12.write(r, 2, u.p90_hosts)
            r += 1
            bk.header(ws12, r, ["hosts", "runs"])
            r += 1
            for k, n in sorted(u.histogram.items()):
                ws12.write(r, 0, k)
                ws12.write(r, 1, n)
                r += 1
            bk.header(ws12, r, ["parameter", "low", "high", "C_req low", "C_req high", "hosts low", "hosts high"])
            r += 1
            for x in u.tornado:
                for j, k2 in enumerate(["parameter", "low", "high", "c_req_low", "c_req_high", "hosts_low",
                                        "hosts_high"]):
                    ws12.write(r, j, x[k2])
                r += 1
        for s in c["d"].inventory_sensitivity:
            ws12.write(r, 0, f"assumed util {s['assumed_util']:.0%}")
            ws12.write(r, 1, s["c_req"])
            ws12.write(r, 2, s.get("hosts", ""))
            r += 1
        r += 2
    r = 3
    for c in clusters:
        ws13.write(r, 0, c["d"].cluster, bk.b)
        r += 1
        for x in c["cs"].recommendations:
            ws13.write(r, 0, x)
            r += 1
        r += 1

    # ---- 14 Appendix -----------------------------------------------------------------------------------
    r = 3
    ws14.write(r, 0, t("config_values"), bk.b)
    r += 1
    from refreshsizer.report.explain import fmt

    for k, v in run.policy.items():
        ws14.write(r, 0, k)
        ws14.write(r, 1, fmt(v))
        r += 1
    r += 1
    ws14.write(r, 0, t("raw_data"), bk.b)
    r += 1
    for cl in project.clusters:
        for h in cl.hosts:
            ws14.write_row(r, 0, [cl.name, h.host_id, h.cpu_model or "", h.sockets or "", h.total_cores or "",
                                  h.ram_gb or "", h.cpu_util_p95_pct or "", h.cpu_util_peak_pct or ""])
            r += 1
    bk.wb.close()
    return Path(out)


def ctx_cpu_ghz(c: dict[str, Any]) -> float:
    cpu = c.get("cpu")
    return float(cpu.base_ghz) if cpu else 0.0
