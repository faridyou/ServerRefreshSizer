"""Naive comparisons, always shown: core match and GHz match (section 7.12)."""

from __future__ import annotations

import math

from refreshsizer.config.schema import Policy
from refreshsizer.engine.growth import growth_factor
from refreshsizer.engine.ha import k_ha_for
from refreshsizer.engine.results import ClusterDemand, NaiveResult
from refreshsizer.model.entities import Explanation


def used_share(d: ClusterDemand) -> tuple[float, str]:
    """Share of the installed source cores actually used (1.0 when unknown).

    Preferred: the measured demand expressed in today's cores (D0 / source SPEC per core), summed per source
    cluster - correct even when some sources have no clock speed (unknown CPU) or no hosts (new VMs). Fallback:
    used GHz / installed GHz. The same measured load as the recommended sizing, so both naive methods differ from it
    only by ignoring the per-core performance gain."""
    if d.used_cores_equiv > 0 and d.source_cores > 0:
        return d.used_cores_equiv / d.source_cores, "measured demand / source SPEC per core"
    if d.source_ghz_installed > 0 and d.source_ghz_used > 0:
        return min(d.source_ghz_used / d.source_ghz_installed, 1.5), "used GHz / installed GHz"
    return 1.0, "no GHz data: installed cores"


def naive(d: ClusterDemand, target_cores_per_host: int, target_base_ghz: float, policy: Policy) -> NaiveResult:
    f = growth_factor(d.growth_cpu_pct, d.years)
    share, basis = used_share(d)
    cores_used = d.used_cores_equiv if d.used_cores_equiv > 0 else d.source_cores * share
    core_need = cores_used * f / d.u_max
    n_c = max(1, math.ceil(core_need / target_cores_per_host - 1e-9))
    k_c = k_ha_for(n_c, policy)
    ghz_need = d.source_ghz_used * f / d.u_max
    n_g = max(1, math.ceil(ghz_need / (target_cores_per_host * target_base_ghz) - 1e-9)) if target_base_ghz else 0
    k_g = k_ha_for(n_g, policy)
    e1 = Explanation(step_id="naive_core", title="Naive core match",
                     formula_latex=r"N = \lceil cores_{src} \times \frac{GHz_{used}}{GHz_{installed}} (1+g)^T / U_{max} "
                                   r"/ cores_{host} \rceil + k_{HA}",
                     formula_text=(f"used cores {cores_used:,.1f} of {d.source_cores} installed ({basis}); "
                                   f"ceil({cores_used:,.1f} x {f:.4f} / {d.u_max} / {target_cores_per_host}) + {k_c} "
                                   f"= {n_c} + {k_c} = {n_c + k_c} hosts"),
                     inputs={"source_cores": d.source_cores, "used_share": round(share, 4), "used_cores":
                             round(cores_used, 2), "growth_factor": round(f, 4), "U_max": d.u_max,
                             "target_cores_per_host": target_cores_per_host}, output=n_c + k_c, unit="hosts",
                     assumptions=["same measured load as the recommendation; ignores the per-core performance gain"])
    e2 = Explanation(step_id="naive_ghz", title="Naive GHz match",
                     formula_latex=r"N = \lceil GHz_{used} (1+g)^T / U_{max} / (cores \times GHz_{base}) \rceil + k_{HA}",
                     formula_text=f"ceil({d.source_ghz_used:,.1f} x {f:.4f} / {d.u_max} / ({target_cores_per_host} x "
                                  f"{target_base_ghz})) + {k_g} = {n_g + k_g} hosts",
                     inputs={"ghz_used": round(d.source_ghz_used, 2), "ghz_installed": round(d.source_ghz_installed, 2),
                             "target_base_ghz": target_base_ghz},
                     output=n_g + k_g, unit="hosts",
                     assumptions=["same measured load as the recommendation; ignores the per-core performance gain"])
    return NaiveResult(core_match_hosts=n_c + k_c, ghz_match_hosts=n_g + k_g, explanations=[e1, e2])
