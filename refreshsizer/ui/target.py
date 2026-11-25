"""Target server selection (Dell platforms, CPU vendor, CPUs) for the sizing forms."""

from __future__ import annotations

from typing import Any

from refreshsizer.catalog.loader import load_cpus, load_platforms
from refreshsizer.config.schema import Policy

VENDORS = ["Intel", "AMD"]
# selector order: the default first (Nutanix AHV + PowerStore, disaggregated)
SELECTOR_STACKS = [("nutanix_ahv", "Nutanix AHV"), ("vmware_vsan", "VMware vSphere (vSAN ESA when HCI)"),
                   ("nutanix_esxi", "Nutanix on VMware ESXi (HCI only)")]


def _default(key: str) -> object:
    from refreshsizer.config.schema import default_policy_dict

    return default_policy_dict().get(key)


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
    from refreshsizer.config.schema import default_policy_dict

    dflt = default_policy_dict()
    stack = policy.target_stack if policy else dflt["target_stack"]
    from refreshsizer.catalog.loader import load_powerstore
    from refreshsizer.config.schema import PROTOCOLS

    es = policy.external_storage if policy else None
    arch = policy.storage_architecture if policy else dflt["storage_architecture"]
    cat = load_powerstore()
    gens = set(cat.get("default_generations") or [])
    arrays = [{"model": m["model"], "media": "/".join((m.get("drives") or {m["media"]: []}).keys()),
               "gen": m.get("generation"),
               "checked": m["model"] in es.models if es and es.models else (not gens or m.get("generation") in gens)}
              for m in cat["models"]]
    storage = {"arch": "hci" if arch == "vsan_esa" else arch, "array": es.array if es else "powerstore",
               "protocol": es.protocol if es else "fc32", "protocols": list(PROTOCOLS.items()), "models": arrays}
    return {"consolidation": policy.consolidation if policy else "single", "storage": storage, "groups": groups, "vendor": vendor, "any_cpu": not sel_c, "text": text, "stack": stack,
            "stacks": SELECTOR_STACKS,
            "cvm": (policy.nutanix_cvm_vcpu, policy.nutanix_cvm_memory_gb) if policy else (12, 48)}


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
    out: dict[str, Any] = {"target_platforms": None if set(chosen) == set(plats) else chosen,
                           "target_cpu_vendors": vendors, "target_cpus": cpus or None}
    stack = str(form.get("stack", "") or "")
    if stack:
        out["target_stack"] = None if stack == _default("target_stack") else stack  # None = back to the default
    cons = str(form.get("consolidation", "") or "")
    if cons in ("single", "per_cluster"):
        out["consolidation"] = None if cons == _default("consolidation") else cons  # None = back to the default
    arch = str(form.get("arch", "") or "")
    if arch in ("auto", "hci", "external"):
        out["storage_architecture"] = None if arch == _default("storage_architecture") else arch
        from refreshsizer.catalog.loader import load_powerstore
        from refreshsizer.config.schema import PROTOCOLS

        known = [m["model"] for m in load_powerstore()["models"]]
        models = [m for m in form.getlist("ps_model") if m in known]
        es: dict[str, Any] = {"models": None if not models or set(models) == set(known) else models}
        if str(form.get("es_array", "")) in ("powerstore", "existing"):
            es["array"] = str(form.get("es_array"))
        if str(form.get("es_protocol", "")) in PROTOCOLS:
            es["protocol"] = str(form.get("es_protocol"))
        out["external_storage"] = es
    return out


def merge_into_saved(saved: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    from refreshsizer.config.schema import deep_merge

    out = dict(saved)
    for k, v in target.items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)  # partial section (external_storage): keep the other saved keys
        else:
            out[k] = v
    return out
