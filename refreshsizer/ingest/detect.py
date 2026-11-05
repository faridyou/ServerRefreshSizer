"""Format detection by sheet names and column signatures."""

from __future__ import annotations

from pathlib import Path

from refreshsizer.ingest.common import CSV_EXT, find_col, read_csv_any, read_workbook, sheet_names


def detect_format(path: Path) -> str:
    """Return one of: rvtools, liveoptics, liveoptics_general, manual_template, nmon, awr, timeseries, universal."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in CSV_EXT:
        try:
            df = read_csv_any(path)
        except Exception:
            return "universal"
        cols = [str(c) for c in df.columns]
        from refreshsizer.ingest.awr import is_awr
        from refreshsizer.ingest.nmon import is_nmon

        if is_nmon(cols):
            return "nmon"
        if is_awr(cols):
            return "awr"
        if (find_col(cols, "timestamp", "time", "ts") and find_col(cols, "host", "hostname")
                and find_col(cols, "cpu_pct", "cpu %") and len(cols) <= 5):
            return "timeseries"
        return "universal"
    try:
        names = sheet_names(path)
    except Exception:
        return "universal"
    low = {n.strip().lower() for n in names}
    if {"vinfo", "vhost"} <= low or ("vinfo" in low and "vcpu" in low):
        return "rvtools"
    if "esx hosts" in low or "esx performance" in low:
        return "liveoptics"
    if "servers" in low and ("hypervisors" in low or "server disks" in low or "vms" in low):
        # General Excel has these tab names; verify Live Optics-specific column
        sheets = read_workbook(path)
        for k, df in sheets.items():
            if k.lower() == "servers" and find_col(df.columns, "Memory (KiB)", "Peak CPU Percentage"):
                return "liveoptics"
    from refreshsizer.ingest.manual_template import is_manual_template

    if is_manual_template(names):
        sheets = read_workbook(path)
        for k, df in sheets.items():
            if k.lower() in ("hosts", "hotes") and find_col(df.columns, "host_id", "id_hote"):
                return "manual_template"
    return "universal"
