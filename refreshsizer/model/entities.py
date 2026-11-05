"""Canonical data model (pydantic v2). Every parser and the universal ingest end up here."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    VERY_LOW = "VERY_LOW"

    @property
    def rank(self) -> int:
        return {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "VERY_LOW": 0}[self.value]

    @staticmethod
    def lowest(*items: Confidence) -> Confidence:
        return min(items, key=lambda c: c.rank) if items else Confidence.HIGH

    def lowered(self) -> Confidence:
        order = [Confidence.VERY_LOW, Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH]
        return order[max(0, self.rank - 1)]


class SizingMode(StrEnum):
    MEASURED = "MEASURED"
    SUMMARY = "SUMMARY"
    SNAPSHOT = "SNAPSHOT"
    INVENTORY_ONLY = "INVENTORY_ONLY"


WORKLOAD_CLASSES = [
    "general_virt", "oltp_db", "dwh", "analytics", "sap_app", "sap_hana", "vdi", "ms_apps",
    "java_web", "hpc", "ai_inference", "containers", "storage_heavy",
]
PLATFORM_TYPES = ["x86", "POWER", "SPARC", "Itanium", "PA-RISC"]
HYPERVISORS = ["vSphere", "Hyper-V", "AHV", "KVM", "PowerVM", "LDOM", "bare-metal"]
LICENSING = ["none", "oracle_ee", "oracle_se2", "sql_ent", "sql_std", "windows_dc", "rhel"]
CRITICALITY = ["tier0", "tier1", "tier2", "tier3"]


class CpuModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    vendor: str
    family: str
    codename: str = ""
    model: str
    cores: int
    threads_per_core: int = 2
    base_ghz: float = 0.0
    turbo_ghz: float | None = None
    allcore_ghz: float | None = None
    l3_mb: float | None = None
    tdp_w: float | None = None
    mem_channels: int | None = None
    max_mt_s: int | None = None
    socket: str | None = None
    max_sockets: int = 2
    isa_features: list[str] = Field(default_factory=list)
    core_type: str = "other"  # P, E, Zen5, Zen5c, POWER, SPARC, other
    oracle_core_factor: float = 0.5
    list_price_usd: float | None = None
    source_url: str | None = None
    sizable: bool = True
    notes: str | None = None


class BenchmarkResult(BaseModel):
    id: str
    suite: str  # cpu2017, cpu2026, cpu2006, rperf, cpw, saps, specjbb2015, vmmark4, tpce
    metric: str  # e.g. SPECrate2017_int_base
    value_base: float | None = None
    value_peak: float | None = None
    system_vendor: str | None = None
    system_name: str | None = None
    cpu_name: str | None = None
    cpu_model_id: str | None = None
    chips: int | None = None
    enabled_cores: int | None = None
    threads_per_core: int | None = None
    memory_desc: str | None = None
    test_date: str | None = None
    hw_avail: str | None = None
    sponsor: str | None = None
    software: str | None = None
    result_url: str | None = None
    retrieved_at: str | None = None
    smt_mode: int | None = None
    source: str = "curated"  # live | curated | user
    notes: str | None = None

    @property
    def per_core(self) -> float | None:
        if self.value_base and self.enabled_cores:
            return self.value_base / self.enabled_cores
        return None


class TimeSeriesPoint(BaseModel):
    ts: str
    host_id: str
    cpu_pct: float  # 0-100


class SourceHost(BaseModel):
    model_config = ConfigDict(extra="allow")
    host_id: str
    cluster: str = "default"
    site: str | None = None
    datacenter: str | None = None
    vendor: str | None = None
    model: str | None = None
    cpu_model: str | None = None
    sockets: int | None = None
    cores_per_socket: int | None = None
    total_cores: int | None = None
    threads_per_core: int | None = None
    nominal_ghz: float | None = None
    ram_gb: float | None = None
    cpu_util_p95_pct: float | None = None
    cpu_util_peak_pct: float | None = None
    cpu_util_avg_pct: float | None = None
    cpu_util_snapshot_pct: float | None = None
    mem_used_gb: float | None = None
    mem_util_peak_pct: float | None = None
    smt_mode: int | None = None
    platform_type: str = "x86"
    hypervisor: str | None = None
    iops_p95: float | None = None
    iops_peak: float | None = None
    iops_avg: float | None = None
    throughput_mbps_peak: float | None = None
    in_maintenance: bool = False
    rperf_system: str | None = None  # key in ibm_rperf.yaml for POWER hosts
    notes: str | None = None
    assumptions: list[str] = Field(default_factory=list)

    @property
    def cores(self) -> int | None:
        if self.total_cores:
            return int(self.total_cores)
        if self.sockets and self.cores_per_socket:
            return int(self.sockets * self.cores_per_socket)
        return None


class SourceWorkload(BaseModel):
    model_config = ConfigDict(extra="allow")
    workload_id: str
    host_id: str | None = None
    cluster: str = "default"
    name: str | None = None
    workload_class: str = "general_virt"
    vcpu: float | None = None
    ram_gb_configured: float | None = None
    ram_gb_active: float | None = None
    ram_gb_consumed: float | None = None
    physc_p95: float | None = None
    db_cpu_p95_cores: float | None = None
    cpu_ghz_p95: float | None = None
    cpu_ghz_peak: float | None = None
    cpu_ghz_avg: float | None = None
    cpu_mhz_snapshot: float | None = None
    storage_used_gib: float | None = None
    storage_provisioned_gib: float | None = None
    iops_p95: float | None = None
    iops_peak: float | None = None
    read_pct: float | None = None
    throughput_mbps_p95: float | None = None
    latency_target_ms: float | None = None
    licensing: str = "none"
    growth_override_pct: float | None = None
    criticality: str = "tier2"
    powered_on: bool = True
    is_template: bool = False
    os: str | None = None
    # POWER LPAR context
    system_model: str | None = None
    smt_mode: int | None = None
    # source CPU for AWR / standalone workloads
    cpu_model: str | None = None
    host_cores: int | None = None


class StorageArray(BaseModel):
    name: str
    cluster: str = "default"
    raw_tib: float | None = None
    used_tib: float | None = None
    drr_observed: float | None = None
    iops_p95: float | None = None
    mbps_p95: float | None = None


class Cluster(BaseModel):
    name: str
    hosts: list[SourceHost] = Field(default_factory=list)
    workloads: list[SourceWorkload] = Field(default_factory=list)
    storage: list[StorageArray] = Field(default_factory=list)
    timeseries: list[TimeSeriesPoint] = Field(default_factory=list)
    utilization_source: str | None = None  # rvtools | liveoptics | manual | nmon | awr | universal
    is_db_split: bool = False

    @property
    def platform_type(self) -> str:
        types = {h.platform_type for h in self.hosts}
        if not types and any(w.physc_p95 is not None for w in self.workloads):
            return "POWER"
        if "POWER" in types:
            return "POWER"
        if "SPARC" in types:
            return "SPARC"
        return next(iter(types)) if len(types) == 1 else "x86"


class Explanation(BaseModel):
    step_id: str
    title: str
    formula_latex: str = ""
    formula_text: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    unit: str = ""
    source_refs: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.HIGH


class DataQuality(BaseModel):
    cluster: str = "default"
    mode: SizingMode = SizingMode.INVENTORY_ONLY
    confidence: Confidence = Confidence.VERY_LOW
    flags: list[str] = Field(default_factory=list)
    customer_questions: list[str] = Field(default_factory=list)


class Assumption(BaseModel):
    text: str
    field: str | None = None
    entity: str | None = None
    category: str = "ASSUMPTION"  # ASSUMPTION | ESTIMATE | WARNING


class MappingEntry(BaseModel):
    file: str
    sheet: str
    column: str
    target: str | None
    unit: str | None = None
    confidence: float = 0.0
    method: str = "rules"  # rules | value | llm | profile | user | parser
    sample: str | None = None
    transformed: str | None = None
    accepted: bool = True


class SheetUse(BaseModel):
    file: str
    sheet: str
    used: bool
    classification: str = "irrelevant"
    reason: str = ""
    rows: int = 0


class IngestSummary(BaseModel):
    files: list[str] = Field(default_factory=list)
    formats: dict[str, str] = Field(default_factory=dict)
    sheets: list[SheetUse] = Field(default_factory=list)
    mappings: list[MappingEntry] = Field(default_factory=list)
    transformations: list[str] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    unmapped_columns: list[str] = Field(default_factory=list)
    llm_used: bool = False
    llm_status: str = "disabled"


class Project(BaseModel):
    name: str = "RefreshSizer project"
    created: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    clusters: list[Cluster] = Field(default_factory=list)
    policy_overrides: dict[str, Any] = Field(default_factory=dict)
    ingest: IngestSummary = Field(default_factory=IngestSummary)
    data_quality: list[DataQuality] = Field(default_factory=list)
    customer_questions: list[str] = Field(default_factory=list)
    customer_questions_fr: list[str] = Field(default_factory=list)
    sizing: dict[str, Any] | None = None  # serialized engine output (SizingRun)

    def cluster(self, name: str) -> Cluster:
        for c in self.clusters:
            if c.name == name:
                return c
        c = Cluster(name=name)
        self.clusters.append(c)
        return c


# --- target side -----------------------------------------------------------


class TargetPlatform(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str
    form_factor_u: int
    sockets: int
    cpu_family: str
    dimm_slots: int
    channels_per_socket: int
    dpc_max: int
    max_mem_tb: float
    mem_speed_by_dpc: dict[int, int] = Field(default_factory=dict)
    dimm_sizes_gb: dict[str, list[int]] = Field(default_factory=dict)  # by core_type or "default"
    drive_bays: int
    drive_form: str = "2.5in NVMe"
    pcie_slots: int = 4
    ocp: list[str] = Field(default_factory=list)
    nic_options: list[str] = Field(default_factory=list)
    psu_options: list[str] = Field(default_factory=list)
    base_power_w: float = 250
    supported_cpu_ids: list[str] = Field(default_factory=list)
    notes: str | None = None


class DimmConfig(BaseModel):
    dimm_size_gb: int
    dimm_count: int
    dpc: int
    speed_mt_s: int
    total_gb: int
    channels_populated: int
    balanced: bool = True
    gbs_per_core: float | None = None


class DriveConfig(BaseModel):
    drive_tb: float
    count: int
    raw_tb: float
    raw_tib: float


class TargetConfig(BaseModel):
    platform: str
    cpu_id: str
    cpu_name: str
    cpu_count: int
    cores_per_cpu: int
    dimm: DimmConfig | None = None
    drives: DriveConfig | None = None
    nics: str | None = None
    psu: str | None = None

    @property
    def total_cores(self) -> int:
        return self.cpu_count * self.cores_per_cpu

    def label(self) -> str:
        return f"{self.platform} {self.cpu_count}x {self.cpu_name}"
