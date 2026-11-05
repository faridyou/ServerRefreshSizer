"""Monte Carlo uncertainty (5,000 runs, seed 42) and sensitivity tornado (section 7.14)."""

from __future__ import annotations

import math

import numpy as np

from refreshsizer.config.schema import Policy
from refreshsizer.engine import ha
from refreshsizer.engine.results import Candidate, ClusterDemand, Uncertainty

RISC = {"POWER", "SPARC", "Itanium", "PA-RISC"}


def _hosts(c_req: float, d_t: float, su: float, policy: Policy, protection: str, floor: int) -> int:
    n_active = max(1, math.ceil(c_req / su - 1e-9))
    k = ha.k_ha_for(n_active, policy)
    n_min, _ = ha.min_hosts(protection, policy)
    return max(n_active + k + (1 if policy.maintenance_reserve else 0), n_min, floor)


def run(d: ClusterDemand, c: Candidate, policy: Policy) -> Uncertainty:
    n_runs = int(policy.monte_carlo_runs)
    rng = np.random.default_rng(int(policy.monte_carlo_seed))
    years = d.years
    g0 = d.growth_cpu_pct
    base_growth = (1 + g0 / 100) ** years
    d0a = d.d0_adj
    risc = d.platform_type in RISC
    u = rng.uniform(0.9, 1.1, n_runs)
    g = rng.uniform(g0 - 3, g0 + 3, n_runs)
    if d.spc_source and d.spc_source_max > d.spc_source_min:
        s_src = rng.uniform(d.spc_source_min, d.spc_source_max, n_runs) / d.spc_source
    else:
        s_src = rng.uniform(0.97, 1.03, n_runs)
    s_tgt = rng.uniform(0.97, 1.03, n_runs)
    f_ratio = np.ones(n_runs)
    ka_ratio = np.ones(n_runs)
    params = {"U_p95": "+/-10 %", "growth_pct": f"{g0:.1f} +/- 3 points", "SPEC_source": "min/max of matching "
              "results (else +/-3 %)", "SPEC_target": "+/-3 %"}
    if risc and d.rperf_factor:
        f_ratio = rng.uniform(0.365, 0.425, n_runs) / d.rperf_factor
        lo, hi = policy.k_arch_range.get("low", 1.10), policy.k_arch_range.get("high", 1.25)
        ka_ratio = rng.uniform(lo, hi, n_runs) / d.k_arch
        params.update({"rperf_F": "0.365-0.425", "k_arch": f"{lo}-{hi}"})
    if d.mode.value == "INVENTORY_ONLY":
        u = rng.uniform(0.5, 1.0, n_runs)
        params["U_p95"] = "inventory-only: assumed utilization 50-100 %"
    d_t = d0a * u * s_src * f_ratio * ka_ratio * (1 + g / 100) ** years
    extra = d.d_t - d0a * base_growth  # step events
    d_t = d_t + extra
    c_req = d_t / d.u_max if d.apply_headroom else d_t
    su = c.su_host_effective * s_tgt
    n_active = np.maximum(1, np.ceil(c_req / su - 1e-9)).astype(int)
    k = np.where(n_active + 1 > policy.ha_n2_threshold_hosts, 2, 1) if policy.ha_policy == "auto" else \
        np.full(n_runs, ha.k_ha_for(1, policy))
    n_min, _ = ha.min_hosts(d.protection, policy)
    cpu_det = _hosts(d.c_req, d.d_t, c.su_host_effective, policy, d.protection, 0)
    floor = c.n_total if c.n_total > cpu_det else 0
    n = np.maximum(np.maximum(n_active + k + (1 if policy.maintenance_reserve else 0), n_min), floor)
    p50 = int(np.percentile(n, 50, method="inverted_cdf"))
    p90 = int(np.percentile(n, 90, method="inverted_cdf"))
    vals, counts = np.unique(n, return_counts=True)
    # tornado
    tornado = []

    def one(label: str, factor_lo: float, factor_hi: float, lo_lbl: str, hi_lbl: str) -> None:
        res = []
        for fct in (factor_lo, factor_hi):
            dt = d.d_t * fct
            cr = dt / d.u_max if d.apply_headroom else dt
            res.append((round(cr, 1), _hosts(cr, dt, c.su_host_effective, policy, d.protection, floor)))
        tornado.append({"parameter": label, "low": lo_lbl, "high": hi_lbl, "c_req_low": res[0][0],
                        "c_req_high": res[1][0], "hosts_low": res[0][1], "hosts_high": res[1][1]})

    gl, gh = ((1 + (g0 - 3) / 100) / (1 + g0 / 100)) ** years, ((1 + (g0 + 3) / 100) / (1 + g0 / 100)) ** years
    one("growth g", gl, gh, f"{g0 - 3:.1f} %", f"{g0 + 3:.1f} %")
    one("utilization U", 0.9, 1.1, "-10 %", "+10 %")
    if d.spc_source:
        one("source SPEC", d.spc_source_min / d.spc_source, d.spc_source_max / d.spc_source,
            f"{d.spc_source_min:.2f}", f"{d.spc_source_max:.2f}")
    if risc and d.rperf_factor:
        one("rPerf factor F", 0.365 / d.rperf_factor, 0.425 / d.rperf_factor, "0.365", "0.425")
    tornado.sort(key=lambda t: abs(t["c_req_high"] - t["c_req_low"]), reverse=True)
    return Uncertainty(runs=n_runs, seed=int(policy.monte_carlo_seed), p50_hosts=p50, p90_hosts=p90,
                       mean_hosts=round(float(n.mean()), 3),
                       histogram={int(v): int(cn) for v, cn in zip(vals, counts, strict=True)},
                       tornado=tornado, parameters=params)
