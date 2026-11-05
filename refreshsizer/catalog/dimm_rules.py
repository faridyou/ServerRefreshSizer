"""DIMM population rules: every channel populated identically, 1 DPC preferred, smallest DIMM that fits."""

from __future__ import annotations

from refreshsizer.model.entities import DimmConfig, TargetPlatform


def dimm_sizes_for(platform: TargetPlatform, core_type: str) -> list[int]:
    sizes = platform.dimm_sizes_gb
    if core_type == "E" and "E" in sizes:
        return sorted(sizes["E"])
    return sorted(sizes.get("default", [16, 32, 64, 96, 128, 256]))


def choose_dimms(platform: TargetPlatform, cpu_count: int, cores_per_cpu: int, core_type: str,
                 cpu_max_mt_s: int | None, need_gb: float) -> DimmConfig | None:
    """Return the balanced DIMM config with the fewest DPC and smallest DIMM >= need_gb, or None."""
    channels = platform.channels_per_socket * cpu_count
    for dpc in range(1, platform.dpc_max + 1):
        for size in dimm_sizes_for(platform, core_type):
            total = size * channels * dpc
            if total > platform.max_mem_tb * 1024 + 1e-6:
                continue
            if total + 1e-9 >= need_gb:
                speed = int(platform.mem_speed_by_dpc.get(dpc, min(platform.mem_speed_by_dpc.values())))
                if cpu_max_mt_s:
                    speed = min(speed, int(cpu_max_mt_s))
                gbs = platform.channels_per_socket * speed * 8 / 1000.0 / max(cores_per_cpu, 1)
                return DimmConfig(dimm_size_gb=size, dimm_count=channels * dpc, dpc=dpc, speed_mt_s=speed,
                                  total_gb=total, channels_populated=channels, balanced=True,
                                  gbs_per_core=round(gbs, 2))
    return None


def is_balanced(cfg: DimmConfig, platform: TargetPlatform, cpu_count: int) -> bool:
    channels = platform.channels_per_socket * cpu_count
    return cfg.dimm_count % channels == 0 and cfg.dimm_count // channels == cfg.dpc and cfg.dpc <= platform.dpc_max


def slot_diagram(cfg: DimmConfig, platform: TargetPlatform, cpu_count: int) -> list[str]:
    """Text diagram: one line per CPU, one cell per channel ([96] = populated DIMM)."""
    lines = []
    for s in range(cpu_count):
        cells = []
        for _ in range(platform.channels_per_socket):
            slots = [f"{cfg.dimm_size_gb}" if d < cfg.dpc else "--" for d in range(platform.dpc_max)]
            cells.append("[" + "|".join(slots) + "]")
        lines.append(f"CPU{s + 1}: " + " ".join(cells))
    return lines
