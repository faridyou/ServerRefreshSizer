"""Network sizing (section 7.11)."""

from __future__ import annotations

from refreshsizer.config.schema import Policy
from refreshsizer.model.entities import Explanation

NIC_MBPS = {"2x25GbE": 2 * 25000 / 8, "2x100GbE": 2 * 100000 / 8}


def choose_nics(mbps_t: float, n_active: int, vsan: bool, policy: Policy) -> tuple[str, Explanation, list[str]]:
    warn: list[str] = []
    per_host = (mbps_t / max(n_active, 1)) + policy.network_vmotion_mbps + policy.network_replication_mbps
    nic = "2x100GbE" if vsan else "2x25GbE"
    reason = "vSAN ESA all-NVMe HCI: 2 x 100 GbE recommended (>= 25 GbE required)" if vsan else "2 x 25 GbE"
    usable = NIC_MBPS[nic] / 2  # one link may fail
    if per_host > usable and nic == "2x25GbE":
        nic = "2x100GbE"
        usable = NIC_MBPS[nic] / 2
        reason += "; upgraded to 2 x 100 GbE for throughput"
    if per_host > usable:
        warn.append(f"per-host network demand {per_host:,.0f} MB/s exceeds one 100 GbE link; add NICs")
    expl = Explanation(
        step_id="network", title="Network per host",
        formula_text=(f"peak {mbps_t:,.0f} MB/s / {n_active} active hosts + vMotion {policy.network_vmotion_mbps:g} "
                      f"+ replication {policy.network_replication_mbps:g} = {per_host:,.0f} MB/s per host; "
                      f"{nic} usable with one link down = {usable:,.0f} MB/s"),
        inputs={"mbps_t": round(mbps_t), "n_active": n_active, "vsan": vsan}, output=nic, unit="", assumptions=warn,
        source_refs=[reason])
    return nic, expl, warn
