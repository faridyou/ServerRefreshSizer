"""Host count with HA (section 7.7)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from refreshsizer.config.schema import Policy
from refreshsizer.engine.storage import MIN_HOSTS
from refreshsizer.model.entities import Explanation


@dataclass
class HaPlan:
    n_active: int
    k_ha: int
    n_total: int
    util_normal: float
    util_failed: float
    n_min: int
    n_min_reason: str
    explanation: Explanation


def k_ha_for(n_active: int, policy: Policy) -> int:
    p = policy.ha_policy.upper()
    if p in ("N+0", "N+1", "N+2"):
        return int(p[-1])
    return 2 if n_active + 1 > policy.ha_n2_threshold_hosts else 1


def min_hosts(protection: str, policy: Policy) -> tuple[int, str]:
    n = MIN_HOSTS.get(protection, 2)
    return n, (f"minimum {n} hosts for {protection}" if protection != "external" else "minimum 2 hosts for HA")


def plan(d_t: float, c_req: float, su_host: float, policy: Policy, protection: str, n_floor: int = 0,
         u_check: float | None = None) -> HaPlan:
    u_max_check = u_check if u_check is not None else (d_t / c_req if c_req else 0.75)
    n_active = max(1, math.ceil(c_req / su_host - 1e-9)) if su_host > 0 else 9999
    k = k_ha_for(n_active, policy)
    maint = 1 if policy.maintenance_reserve else 0
    n_min, reason = min_hosts(protection, policy)
    n_total = max(n_active + k + maint, n_min, n_floor)
    if policy.stretched_cluster:
        n_total = 2 * max(math.ceil(n_total / 2), n_active + k)
        reason += "; stretched cluster: each site carries full load (+ witness)"
    while su_host > 0 and (n_total - k - maint) > 0 and d_t / ((n_total - k - maint) * su_host) > u_max_check + 1e-12:
        n_total += 1
    if n_total - k - maint > n_active:
        n_active = n_total - k - maint
    u_n = d_t / (n_total * su_host) if su_host else 0
    u_f = d_t / ((n_total - k - maint) * su_host) if su_host and n_total - k - maint > 0 else 1.0
    expl = Explanation(
        step_id="ha", title="Host count with HA",
        formula_latex=r"N_{active} = \lceil C_{req} / SU_{host} \rceil;\ N_{total} = N_{active} + k_{HA}",
        formula_text=(f"ceil({c_req:,.1f} / {su_host:,.1f}) = {math.ceil(c_req / su_host - 1e-9) if su_host else 0}"
                      f" active; + {k} HA{' + 1 maintenance' if maint else ''} -> {n_total} hosts "
                      f"(utilization {u_n:.1%} normal, {u_f:.1%} with {k} host(s) failed; {reason})"),
        inputs={"C_req": round(c_req, 2), "SU_host": round(su_host, 2), "k_HA": k, "n_min": n_min},
        output=n_total, unit="hosts")
    return HaPlan(n_active, k, n_total, u_n, u_f, n_min, reason, expl)
