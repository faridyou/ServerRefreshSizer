"""Target server selection (Dell platforms, CPU vendor, CPUs) for the sizing forms."""

from __future__ import annotations

from typing import Any

from refreshsizer.catalog.loader import load_cpus, load_platforms
from refreshsizer.config.schema import Policy

VENDORS = ["Intel", "AMD"]


def platform_vendor(model: str) -> str:
    cpus = load_cpus()
    p = load_platforms()[model]
    vs = {cpus[c].vendor for c in p.supported_cpu_ids if c in cpus}
    return next(iter(vs)) if len(vs) == 1 else "/".join(sorted(vs))


def context(policy: Policy | None) -> dict[str, Any]:
    plats = load_platforms()
    cpus = load_cpus()
    sel_p = set(policy.target_platforms) if policy and policy.target_platforms else set(plats)
    sel_v = {v.lower() for v in policy.target_cpu_vendors} if policy and policy.target_cpu_vendors else None
    sel_c = set(policy.target_cpus) if policy and policy.target_cpus else set()
    groups = []
    for v in VENDORS:
        ps = [{"model": p.model, "sockets": p.sockets, "u": p.form_factor_u, "ncpu": len(p.supported_cpu_ids),
               "checked": p.model in sel_p and (sel_v is None or v.lower() in sel_v)}
              for p in plats.values() if platform_vendor(p.model) == v]
        supported = {c for p in plats.values() for c in p.supported_cpu_ids}
        cs = sorted(({"id": c.id, "model": c.model, "cores": c.cores, "checked": c.id in sel_c}
                     for c in cpus.values() if c.vendor == v and c.id in supported and c.sizable),
                    key=lambda x: str(x["model"]))
        groups.append({"vendor": v, "platforms": ps, "cpus": cs})
    if sel_v is None:
        vendor = "both"
    else:
        vendor = next((v for v in VENDORS if {v.lower()} == sel_v), "both")
    from refreshsizer.engine.optimizer import target_selection_text

    text = target_selection_text(policy) if policy else "all Dell 17G platforms and CPUs"
    return {"groups": groups, "vendor": vendor, "any_cpu": not sel_c, "text": text}


def overrides_from_form(form: Any) -> dict[str, Any]:
    """Explicit target overrides from a submitted selector (None = no restriction, clears saved values)."""
    plats = list(load_platforms())
    chosen = [p for p in form.getlist("plat") if p in plats]
    vendor = str(form.get("vendor", "both"))
    cpus = [c for c in form.getlist("cpu") if c in load_cpus()]
    if not chosen:
        raise ValueError("select at least one target platform")
    vendors = None if vendor == "both" else [{"intel": "Intel", "amd": "AMD"}.get(vendor.lower(), vendor)]
    if vendors:
        chosen = [p for p in chosen if platform_vendor(p) in vendors]
        if not chosen:
            raise ValueError(f"no selected platform uses {vendors[0]} CPUs")
    return {"target_platforms": None if set(chosen) == set(plats) else chosen,
            "target_cpu_vendors": vendors, "target_cpus": cpus or None}


def merge_into_saved(saved: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    out = dict(saved)
    for k, v in target.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = v
    return out
