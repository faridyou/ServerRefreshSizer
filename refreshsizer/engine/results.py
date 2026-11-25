"""Engine output models (serialized into project.json under 'sizing')."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from refreshsizer.model.entities import (
    Confidence,
    DimmConfig,
    DriveConfig,
    Explanation,
    SizingMode,
    TargetConfig,
)


class HostCapacity(BaseModel):
    host_id: str
    cpu_name: str
    cores: int
    sockets: int | None = None
    spc: float
    su_cap: float
    util_used_pct: float | None = None
    util_basis: str | None = None
    demand_su: float | None = None
    method: str = ""
    confidence: Confidence = Confidence.HIGH
    flags: list[str] = Field(default_factory=list)


class ClusterDemand(BaseModel):
    cluster: str
    source_cluster: str
    source_clusters: list[str] = Field(default_factory=list)  # consolidated target: every source cluster
    mode: SizingMode
    confidence: Confidence
    platform_type: str = "x86"
    wl_class: str = "general_virt"
    criticality: str = "tier2"
    is_db_cluster: bool = False
    external_storage: bool = False
    years: int = 5
    # CPU
    su_cap_total: float = 0.0
    spc_source: float = 0.0
    spc_source_min: float = 0.0
    spc_source_max: float = 0.0
    source_cores: int = 0
    source_threads: int = 0
    source_ghz_used: float = 0.0
    source_ghz_installed: float = 0.0  # sum of cores x nominal GHz (0 = unknown)
    source_hosts: int = 0
    d0: float = 0.0
    d0_by_path: dict[str, float] = Field(default_factory=dict)
    k_arch: float = 1.0
    k_virt: float = 1.0
    k_wl: float = 1.0
    d0_adj: float = 0.0
    growth_cpu_pct: float = 15.0
    d_t: float = 0.0
    u_max: float = 0.75
    c_req: float = 0.0
    c_req_no_headroom: float = 0.0
    apply_headroom: bool = True
    rperf_factor: float | None = None
    rperf_factor_matched: float | None = None
    # memory (GB = GiB)
    mem_basis: str = "configured"
    m0: float = 0.0
    growth_mem_pct: float = 10.0
    m_t: float = 0.0
    # storage (TiB)
    storage_used0_tib: float = 0.0
    storage_used_t_tib: float = 0.0
    storage_effective_tib: float = 0.0
    storage_raw_tib: float = 0.0
    protection: str = "vsan_esa_raid5_4p1"
    p_prot: float = 1.25
    drr: float = 1.3
    growth_storage_pct: float = 20.0
    iops0: float = 0.0
    iops_t: float = 0.0
    backend_iops_t: float = 0.0
    read_pct: float = 70.0
    mbps0: float = 0.0
    mbps_t: float = 0.0
    # workloads
    vm_count: int = 0
    inactive_vms: int = 0  # powered-off VMs / templates excluded from CPU and memory
    inactive_storage_tib: float = 0.0  # their storage, included when storage_include_powered_off
    total_vcpu: float = 0.0
    largest_vm_vcpu: float = 0.0
    licensing: dict[str, dict[str, float]] = Field(default_factory=dict)
    hosts: list[HostCapacity] = Field(default_factory=list)
    explanations: list[Explanation] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    inventory_sensitivity: list[dict[str, Any]] = Field(default_factory=list)


class LicenseLine(BaseModel):
    product: str
    metric: str
    quantity: float
    unit_price: float = 0.0
    one_time_usd: float = 0.0
    annual_usd: float = 0.0
    horizon_usd: float = 0.0
    note: str = ""


class Candidate(BaseModel):
    config: TargetConfig
    label: str
    spc_target: float
    spc_flags: list[str] = Field(default_factory=list)
    spc_confidence: Confidence = Confidence.HIGH
    spc_same_system: bool = False
    su_host: float
    su_host_effective: float
    n_active: int
    k_ha: int
    n_total: int
    n_min_reason: str = ""
    util_normal: float
    util_failed: float
    mem_per_host_need_gb: float = 0.0
    dimm: DimmConfig | None = None
    drives: DriveConfig | None = None
    nics: str = ""
    raw_per_host_tib: float = 0.0
    power_w_host: float = 0.0
    power_w_total: float = 0.0
    rack_units: int = 0
    capex_host: float = 0.0
    capex_total: float = 0.0
    power_opex: float = 0.0
    licenses: list[LicenseLine] = Field(default_factory=list)
    license_total: float = 0.0
    tco: float = 0.0
    licensed_cores_vmware: int = 0
    licensed_cores_db: float = 0.0
    oracle_licenses_active: float = 0.0
    oracle_licenses_all: float = 0.0
    vcpu_ratio_raw: float = 0.0
    vcpu_ratio_norm: float = 0.0
    numa_ok: bool = True
    feasible: bool = True
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    explanations: list[Explanation] = Field(default_factory=list)


class NaiveResult(BaseModel):
    core_match_hosts: int
    ghz_match_hosts: int
    explanations: list[Explanation] = Field(default_factory=list)


class Uncertainty(BaseModel):
    runs: int
    seed: int
    p50_hosts: int
    p90_hosts: int
    mean_hosts: float
    histogram: dict[int, int] = Field(default_factory=dict)
    tornado: list[dict[str, Any]] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ArrayOption(BaseModel):
    model: str
    media: str
    appliances: int
    drives_per_appliance: int
    drive_tb: float
    expansions_per_appliance: int
    raw_tb: float
    raw_tib: float
    usable_tib: float
    effective_tib: float
    fill_today: float
    fill_t: float
    indicative_iops: float
    iops_util: float
    rack_units: int
    power_w: float
    capex_usd: float
    parity_efficiency: float

    @property
    def label(self) -> str:
        n = f"{self.appliances} x " if self.appliances > 1 else ""
        return f"{n}{self.model}, {self.drives_per_appliance} x {self.drive_tb:g} TB NVMe {self.media}"


class StorageDesign(BaseModel):
    architecture: str                      # hci | external
    solution: str
    protection: str
    protection_label: str
    years: int
    used0_tib: float
    growth_pct: float
    inactive_vms: int = 0
    inactive_tib: float = 0.0  # part of used0_tib from powered-off VMs / templates
    used_t_tib: float
    drr: float
    physical_t_tib: float                  # after data reduction
    max_fill: float
    iops0: float
    iops_t: float
    backend_iops_t: float
    read_pct: float
    mbps_t: float
    # HCI
    hosts: int = 0
    drives_per_host: int = 0
    drive_tb: float = 0.0
    raw_tib: float = 0.0
    usable_tib: float = 0.0
    effective_tib: float = 0.0
    fill_today: float = 0.0
    fill_t: float = 0.0
    host_boot: str = ""
    # external
    array_kind: str = ""                   # powerstore | existing
    array: ArrayOption | None = None
    alternatives: list[ArrayOption] = Field(default_factory=list)
    parity: str = ""
    protocol: str = ""
    protocol_label: str = ""
    host_adapter: str = ""
    host_ports: int = 0
    array_ports: int = 0
    fabrics: int = 0
    switch_ports_per_fabric: int = 0
    adapters_capex_usd: float = 0.0
    capex_usd: float = 0.0
    notes: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    explanations: list[Explanation] = Field(default_factory=list)


class ClusterSizing(BaseModel):
    demand: ClusterDemand
    candidates: list[Candidate] = Field(default_factory=list)
    pareto: list[Candidate] = Field(default_factory=list)
    scenarios: list[Candidate] = Field(default_factory=list)  # best configuration per CPU model
    target_selection: str = ""
    all_points: list[tuple[int, float]] = Field(default_factory=list)
    recommended: Candidate | None = None
    storage: StorageDesign | None = None
    naive: NaiveResult | None = None
    uncertainty: Uncertainty | None = None
    source_licensing: list[LicenseLine] = Field(default_factory=list)
    inventory_no_headroom_hosts: int | None = None
    objective: str = "min_tco"
    evaluated: int = 0
    infeasible: int = 0
    warnings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class SizingRun(BaseModel):
    generated_at: str
    policy: dict[str, Any]
    clusters: list[ClusterSizing]
    benchmark_db: dict[str, Any] = Field(default_factory=dict)
    disclaimers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
