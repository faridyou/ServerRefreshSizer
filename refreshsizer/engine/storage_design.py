"""Detailed storage design for the recommended configuration.

HCI (vSAN ESA / Nutanix DSF): capacity on the nodes, from the per-host drive choice.
Disaggregated (servers + hypervisor + external array): a Dell PowerStore array is sized from the same storage
demand (used -> growth -> data reduction -> max fill), with DRE parity, drive/enclosure layout, an indicative
IOPS check and the SAN/IP connectivity for the hosts. The array never changes the host count, the CPU sizing or
the compute TCO of the candidates; its cost is shown separately.
"""

from __future__ import annotations

import math
from typing import Any

from refreshsizer.catalog.loader import load_powerstore
from refreshsizer.config.schema import PROTOCOLS, Policy
from refreshsizer.engine.results import ArrayOption, Candidate, ClusterDemand, StorageDesign
from refreshsizer.engine.storage import TB_TO_TIB
from refreshsizer.model.entities import Explanation

PROT_LABELS = {
    "vsan_esa_raid1": "vSAN ESA RAID-1 (mirror, FTT=1)", "vsan_esa_raid5_4p1": "vSAN ESA RAID-5 4+1 (FTT=1)",
    "vsan_esa_raid5_2p1": "vSAN ESA RAID-5 2+1 (FTT=1)", "vsan_esa_raid6_4p2": "vSAN ESA RAID-6 4+2 (FTT=2)",
    "nutanix_rf2": "Nutanix RF2", "nutanix_rf3": "Nutanix RF3", "nutanix_rf2_ecx_4p1": "Nutanix RF2 + EC-X 4+1",
    "nutanix_rf3_ecx_4p2": "Nutanix RF3 + EC-X 4+2", "external": "external array",
    "external_nutanix": "external array (Nutanix AHV)",
}


def dre_efficiency(n: int, parity: str, cat: dict[str, Any]) -> float:
    eff = 0.0
    for step in cat["dre"][parity]:
        if n >= step["min_drives"]:
            eff = float(step["efficiency"])
    return eff


def usable_tib(n: int, tb: float, parity: str, cat: dict[str, Any]) -> float:
    """Usable TiB of one appliance with n drives of tb TB (DRE simplified, see catalog/powerstore.yaml)."""
    eff = dre_efficiency(n, parity, cat)
    if eff <= 0:
        return 0.0
    spares = math.ceil(n / cat["dre"]["set_size"][parity])
    return max(n - spares, 0) * tb * TB_TO_TIB * eff * (1 - float(cat["dre"]["system_overhead"]))


def _price_drive(prices: dict[str, Any], media: str, tb: float) -> float:
    table = (prices.get("powerstore_drive_usd_per_tb") or {}).get(media) or {}
    return float({float(k): v for k, v in table.items()}.get(tb, 170.0))


def allowed_models(policy: Policy) -> list[dict[str, Any]]:
    """PowerStore models the optimizer may use: the ticked ones, else the current generation(s)."""
    cat = load_powerstore()
    es = policy.external_storage
    if es.models:
        return [m for m in cat["models"] if m["model"] in es.models]
    gens = set(cat.get("default_generations") or [])
    return [m for m in cat["models"] if not gens or m.get("generation") in gens]


def max_appliances(policy: Policy) -> int:
    """Nutanix AHV with PowerStore (NCI 7.6) supports a single-appliance PowerStore cluster only."""
    return 1 if policy.nutanix else int(load_powerstore()["max_appliances"])


def array_options(need_usable_tib: float, physical_today_tib: float, physical_t_tib: float, drr: float,
                  iops_t: float, policy: Policy, prices: dict[str, Any]) -> list[ArrayOption]:
    """Cheapest layout per PowerStore model that meets capacity (max fill) and the indicative IOPS check."""
    cat = load_powerstore()
    es = policy.external_storage
    slots = int(cat["expansion_slots"])
    out: list[ArrayOption] = []
    for m in allowed_models(policy):
        best: ArrayOption | None = None
        min_d = max(int(m["min_drives"]), 7 if es.parity == "double" else 0)
        appl_iops = math.ceil(iops_t / (float(m["indicative_iops"]) * es.iops_max_util)) if iops_t else 1
        sizes = [(media, tb) for media, tbs in (m.get("drives") or {m["media"]: cat["drive_tb"][m["media"]]}).items()
                 for tb in tbs if es.media == "any" or media.lower() == es.media]
        for media, tb in sizes:
            for appl in range(max(1, appl_iops), max_appliances(policy) + 1):
                n = next((k for k in range(min_d, int(m["max_drives"]) + 1)
                          if appl * usable_tib(k, tb, es.parity, cat) >= need_usable_tib), None)
                if n is None:
                    continue
                exp = math.ceil(max(0, n - int(m["base_slots"])) / slots)
                cost = appl * (float((prices.get("powerstore_base_usd") or {}).get(m["model"], 100000))
                               + exp * float(prices.get("powerstore_expansion_usd", 15000))
                               + n * tb * _price_drive(prices, media, tb))
                use = appl * usable_tib(n, tb, es.parity, cat)
                ind = appl * float(m["indicative_iops"])
                opt = ArrayOption(
                    model=m["model"], media=media, appliances=appl, drives_per_appliance=n, drive_tb=tb,
                    expansions_per_appliance=exp, raw_tb=round(appl * n * tb, 2),
                    raw_tib=round(appl * n * tb * TB_TO_TIB, 2), usable_tib=round(use, 2),
                    effective_tib=round(use * drr, 2), fill_today=round(physical_today_tib / use, 4) if use else 0,
                    fill_t=round(physical_t_tib / use, 4) if use else 0, indicative_iops=ind,
                    iops_util=round(iops_t / ind, 4) if ind else 0,
                    rack_units=appl * (int(m["rack_units_base"]) + exp * int(cat["expansion_rack_units"])),
                    power_w=appl * (float(m["power_w_base"]) + exp * float(cat["expansion_power_w"])),
                    capex_usd=round(cost), parity_efficiency=dre_efficiency(n, es.parity, cat))
                if best is None or (opt.capex_usd, opt.appliances, n) < (best.capex_usd, best.appliances,
                                                                          best.drives_per_appliance):
                    best = opt
                break  # more appliances than needed only adds cost
        if best:
            out.append(best)
    return sorted(out, key=lambda o: (o.capex_usd, o.appliances))


def design(d: ClusterDemand, rec: Candidate | None, policy: Policy, prices: dict[str, Any],
           stack_label: str, n_hosts: int | None = None) -> StorageDesign:
    """n_hosts: hosts attached to the array when it is shared by several target clusters (default rec.n_total)."""
    years = d.years
    drr = d.drr
    common: dict[str, Any] = dict(
        protection=d.protection, protection_label=PROT_LABELS.get(d.protection, d.protection), years=years,
        used0_tib=round(d.storage_used0_tib, 2), growth_pct=d.growth_storage_pct, inactive_vms=d.inactive_vms,
        inactive_tib=round(d.inactive_storage_tib, 2),
        used_t_tib=round(d.storage_used_t_tib, 2), iops0=d.iops0, iops_t=d.iops_t, backend_iops_t=d.backend_iops_t,
        read_pct=d.read_pct, mbps_t=d.mbps_t)
    n = n_hosts if n_hosts is not None else (rec.n_total if rec else 0)
    if not d.external_storage:
        phys_t = d.storage_effective_tib
        sd = StorageDesign(architecture="hci", solution=f"{stack_label} (HCI, storage on the server nodes)",
                           drr=round(drr, 2), physical_t_tib=round(phys_t, 2), max_fill=1 - policy.slack, **common)
        if rec and rec.drives:
            raw = rec.drives.raw_tib * n
            usable = raw / d.p_prot
            phys_0 = d.storage_used0_tib / drr if drr else 0
            sd.hosts, sd.drives_per_host, sd.drive_tb = n, rec.drives.count, rec.drives.drive_tb
            sd.raw_tib, sd.usable_tib = round(raw, 2), round(usable, 2)
            sd.effective_tib = round(usable * drr, 2)
            sd.fill_today = round(phys_0 / usable, 4) if usable else 0
            sd.fill_t = round(phys_t / usable, 4) if usable else 0
            sd.host_boot = "BOSS (2 x M.2, RAID-1) for the hypervisor"
            sd.explanations.append(Explanation(
                step_id="hci_capacity", title="HCI capacity",
                formula_text=(f"{n} hosts x {rec.drives.count} x {rec.drives.drive_tb:g} TB = {raw:,.1f} TiB raw; "
                              f"/ {d.p_prot:g} ({sd.protection_label}) = {usable:,.1f} TiB usable; x DRR {drr:.2f} = "
                              f"{usable * drr:,.1f} TiB effective; fill at year {years}: {phys_t:,.1f} / "
                              f"{usable:,.1f} = {sd.fill_t:.0%} (slack keeps it <= {1 - policy.slack:.0%})"),
                inputs={"hosts": n, "drives_per_host": rec.drives.count, "drive_TB": rec.drives.drive_tb,
                        "P_prot": d.p_prot, "DRR": round(drr, 3)}, output=round(usable, 2), unit="TiB"))
        elif d.storage_raw_tib <= 0:
            sd.flags.append("no storage capacity data: storage not sized (customer question raised)")
        return sd

    es = policy.external_storage
    if es.drr is not None:
        drr = float(es.drr)
    phys_t = d.storage_used_t_tib / drr if drr else d.storage_used_t_tib
    phys_0 = d.storage_used0_tib / drr if drr else d.storage_used0_tib
    need = phys_t / es.max_fill
    proto = es.protocol
    if policy.nutanix and proto != "nvme_tcp25":  # Nutanix AHV + external storage: NVMe/TCP only (NCI 7.6)
        proto = "nvme_tcp25"
    sd = StorageDesign(architecture="external", solution="", drr=round(drr, 2), physical_t_tib=round(phys_t, 2),
                       max_fill=es.max_fill, array_kind=es.array, parity=es.parity, protocol=proto,
                       protocol_label=PROTOCOLS[proto], hosts=n, **common)
    sd.host_boot = "BOSS (2 x M.2, RAID-1) for the hypervisor; no data drives in the servers"
    if es.drr is not None:
        sd.notes.append(f"array data reduction {drr:g}:1 set in external_storage.drr (per-class drr not used)")
    sd.explanations.append(Explanation(
        step_id="array_capacity", title="External array capacity requirement",
        formula_latex=r"Usable_{req} = \frac{Used_0 (1+g_s)^T / DRR}{fill_{max}}",
        formula_text=(f"{d.storage_used0_tib:,.2f} TiB x (1 + {d.growth_storage_pct:g} %)^{years} = "
                      f"{d.storage_used_t_tib:,.2f} TiB; / DRR {drr:.2f} = {phys_t:,.2f} TiB physical; / max fill "
                      f"{es.max_fill:.0%} = {need:,.2f} TiB usable required"),
        inputs={"Used0_TiB": round(d.storage_used0_tib, 3), "g_s_pct": d.growth_storage_pct, "T": years,
                "DRR": round(drr, 3), "max_fill": es.max_fill}, output=round(need, 2), unit="TiB"))
    # connectivity (independent of the array model)
    fc = proto in ("fc32", "nvme_fc32")
    sd.fabrics = 2
    sd.host_ports = es.host_ports
    sd.host_adapter = (f"{math.ceil(es.host_ports / 2)} x dual-port 32 Gb FC HBA" if fc else
                       f"{math.ceil(es.host_ports / 2)} x dual-port 25 GbE NIC dedicated to storage")
    sd.adapters_capex_usd = n * math.ceil(es.host_ports / 2) * float(
        (prices.get("storage_adapter_usd") or {}).get(proto, 1000))
    if policy.nutanix:
        sd.notes.append("Nutanix AHV with external storage (NCI 7.6 or later): NVMe/TCP only on dedicated, "
                        "non-aggregated 10 GbE+ ports with one IP per host, single-appliance PowerStore cluster, "
                        "PowerStoreOS 5.0.0.2+, AOS 7.6 / AHV 11.2; check the Dell PowerStore support matrix")
        if es.protocol != proto:
            sd.notes.append(f"protocol {PROTOCOLS[es.protocol]} is not supported by Nutanix with PowerStore: "
                            "NVMe/TCP used instead")
    if es.array == "existing":
        sd.solution = f"{stack_label.split(' + ')[0]} + existing external array ({sd.protocol_label})"
        sd.notes.append("existing array kept: confirm free capacity, performance headroom, support end date and "
                        "compatibility (VMware HCL / Dell E-Lab) for the new hosts")
        sd.array_ports = 4
        sd.switch_ports_per_fabric = n * es.host_ports // 2 + sd.array_ports // 2
        sd.capex_usd = round(sd.adapters_capex_usd)
        return sd
    opts = array_options(need, phys_0, phys_t, drr, d.iops_t, policy, prices)
    if not opts:
        sd.flags.append(f"no PowerStore layout meets {need:,.1f} TiB usable and {d.iops_t:,.0f} IOPS within "
                        f"{max_appliances(policy)} appliance(s): split the estate or review the demand")
        sd.solution = f"{stack_label.split(' + ')[0]} + Dell PowerStore (not sized)"
        return sd
    a = opts[0]
    sd.array, sd.alternatives = a, opts[1:]
    sd.usable_tib, sd.effective_tib, sd.raw_tib = a.usable_tib, a.effective_tib, a.raw_tib
    sd.fill_today, sd.fill_t = a.fill_today, a.fill_t
    sd.array_ports = a.appliances * (8 if n > 16 else 4)
    sd.switch_ports_per_fabric = n * es.host_ports // 2 + sd.array_ports // 2
    sd.capex_usd = round(a.capex_usd + sd.adapters_capex_usd)
    sd.solution = f"{stack_label.split(' + ')[0]} + {a.label.split(',')[0]} ({sd.protocol_label})"
    sd.explanations.append(Explanation(
        step_id="array_layout", title="PowerStore layout",
        formula_text=(f"{a.appliances} appliance(s) x {a.drives_per_appliance} x {a.drive_tb:g} TB {a.media} "
                      f"({a.expansions_per_appliance} expansion enclosure(s) each) = {a.raw_tib:,.1f} TiB raw; DRE "
                      f"{es.parity} parity {a.parity_efficiency:.1%}, 1 spare drive per resiliency set, system "
                      f"overhead -> {a.usable_tib:,.1f} TiB usable (>= {need:,.1f}); x DRR {drr:.2f} = "
                      f"{a.effective_tib:,.1f} TiB effective; fill {a.fill_today:.0%} today, {a.fill_t:.0%} at "
                      f"year {years}; IOPS {d.iops_t:,.0f} / indicative {a.indicative_iops:,.0f} = {a.iops_util:.0%}"),
        inputs={"need_usable_TiB": round(need, 2), "parity": es.parity, "IOPS_T": round(d.iops_t),
                "iops_max_util": es.iops_max_util}, output=a.label, unit="",
        assumptions=["DRE layout simplified and indicative IOPS are placeholders: validate with Dell's "
                     "PowerStore sizing tools before quoting"]))
    if d.storage_used0_tib <= 0:
        sd.flags.append("no storage capacity data: the array is the minimum configuration (customer question)")
    sd.notes.append("indicative IOPS are relative placeholders (catalog/powerstore.yaml), not Dell figures: "
                    "validate performance with Dell's PowerStore sizing tools")
    if fc:
        sd.notes.append(f"two redundant FC fabrics; {sd.switch_ports_per_fabric} switch ports per fabric "
                        "(hosts + array), excluding ISLs and spare ports")
    else:
        sd.notes.append(f"two redundant storage networks (25 GbE, jumbo frames); {sd.switch_ports_per_fabric} "
                        "switch ports per network, separate from the VM/vMotion network")
    return sd
