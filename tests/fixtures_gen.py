"""Generate synthetic input files for tests and demos.

Run:  python tests/fixtures_gen.py [out_dir]

The reference estate (golden tests A and C): 10 ESXi hosts 2x Intel Xeon E5-2680 v4 (14c, 2.4 GHz),
512 GiB each, 120 powered-on VMs = 840 vCPU, 3,600 GiB configured memory, 70 TiB used storage.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

HOSTS = [f"esx{i:02d}.corp.local" for i in range(1, 11)]
CLUSTER = "Prod-Cluster"
CPU_STR = "Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz"
PEAK_PCT = [45, 50, 52, 55, 58, 60, 57, 53, 61, 59]  # sum 550 -> 55 % average
AVG_PCT = [25, 28, 30, 31, 33, 34, 32, 29, 35, 33]
SNAP_PCT = [30, 35, 33, 38, 40, 42, 36, 31, 41, 39]
N_VMS = 120
VCPU_CYCLE = [4, 8, 8, 8]          # avg 7  -> 840 vCPU
MEM_CYCLE = [16, 32, 32, 40]       # avg 30 -> 3,600 GiB
RAM_GIB = 512


def vm_list() -> list[dict]:
    vms = []
    for i in range(N_VMS):
        used = 497 if i % 2 == 0 else 697
        if i == 0:
            used += 40  # total = 71,680 GiB = 70 TiB
        vms.append({
            "name": f"vm{i:03d}", "vcpu": VCPU_CYCLE[i % 4], "mem_gib": MEM_CYCLE[i % 4],
            "used_gib": used, "prov_gib": used * 1.6, "host": HOSTS[i % 10],
            "os": "Microsoft Windows Server 2019 (64-bit)" if i % 3 == 0 else "Red Hat Enterprise Linux 8 (64-bit)",
            "peak_ghz": round(VCPU_CYCLE[i % 4] * 2.4 * 0.45, 2), "avg_ghz": round(VCPU_CYCLE[i % 4] * 2.4 * 0.2, 2),
        })
    return vms


def _ws(wb: Workbook, title: str, header: list[str], rows: list[list]) -> None:
    ws = wb.create_sheet(title)
    ws.append(header)
    for r in rows:
        ws.append(r)


def gen_rvtools(out: Path) -> Path:
    wb = Workbook()
    wb.remove(wb.active)  # type: ignore[arg-type]
    vms = vm_list()
    vinfo = []
    for v in vms:
        vinfo.append([v["name"], "poweredOn", False, v["vcpu"], v["mem_gib"] * 1024, v["prov_gib"] * 1024,
                      v["used_gib"] * 1024, v["os"], v["host"], CLUSTER, "DC1"])
    for i in range(3):
        vinfo.append([f"tmpl{i}", "poweredOff", True, 2, 4096, 40960, 20480, "Linux", HOSTS[0], CLUSTER, "DC1"])
    for i in range(5):
        vinfo.append([f"old{i}", "poweredOff", False, 4, 8192, 102400, 51200, "Linux", HOSTS[1], CLUSTER, "DC1"])
    _ws(wb, "vInfo", ["VM", "Powerstate", "Template", "CPUs", "Memory", "Provisioned MiB", "In Use MiB",
                      "OS according to the configuration file", "Host", "Cluster", "Datacenter"], vinfo)
    vcpu = [[v["name"], v["vcpu"], 2, v["vcpu"] // 2, v["vcpu"] * 2400, int(v["vcpu"] * 2400 * 0.3), 0, -1, 1000]
            for v in vms]
    _ws(wb, "vCPU", ["VM", "CPUs", "Sockets", "Cores p/s", "Max", "Overall", "Reservation", "Limit", "Shares"], vcpu)
    vmem = [[v["name"], v["mem_gib"] * 1024, int(v["mem_gib"] * 1024 * 0.8), int(v["mem_gib"] * 1024 * 0.3), 0, 0, 0]
            for v in vms]
    _ws(wb, "vMemory", ["VM", "Size MiB", "Consumed", "Active", "Ballooned", "Swapped", "Reservation"], vmem)
    vdisk = [[v["name"], "Hard disk 1", int(v["prov_gib"] * 1024), True, "SCSI controller 0"] for v in vms]
    _ws(wb, "vDisk", ["VM", "Disk", "Capacity MiB", "Thin", "Controller"], vdisk)
    vpart = [[v["name"], "C:\\", int(v["prov_gib"] * 1024), v["used_gib"] * 1024,
              int((v["prov_gib"] - v["used_gib"]) * 1024), 37] for v in vms]
    _ws(wb, "vPartition", ["VM", "Disk", "Capacity MiB", "Consumed MiB", "Free MiB", "Free %"], vpart)
    vhost = []
    for i, h in enumerate(HOSTS):
        vhost.append([h, CLUSTER, "DC1", CPU_STR, 2400, 2, 14, 28, SNAP_PCT[i], RAM_GIB * 1024, 62, 84, 360000,
                      "VMware ESXi 7.0.3", "Dell Inc.", "PowerEdge R730", False, True])
    _ws(wb, "vHost", ["Host", "Cluster", "Datacenter", "CPU Model", "Speed", "# CPU", "Cores per CPU", "# Cores",
                      "CPU usage %", "# Memory", "Memory usage %", "# vCPUs", "vRAM", "ESX Version", "Vendor",
                      "Model", "in Maintenance Mode", "HT Active"], vhost)
    _ws(wb, "vDatastore", ["Name", "Capacity MiB", "Free MiB"], [["DS01", 150 * 1024 * 1024, 60 * 1024 * 1024]])
    _ws(wb, "vCluster", ["Name", "NumHosts", "HA enabled"], [[CLUSTER, 10, True]])
    p = out / "rvtools_estate.xlsx"
    wb.save(p)
    return p


def gen_liveoptics(out: Path) -> Path:
    wb = Workbook()
    wb.remove(wb.active)  # type: ignore[arg-type]
    vms = vm_list()
    _ws(wb, "Details", ["Project", "Collection"], [["Synthetic", "7 days"]])
    hosts = [[h, "DC1", "vc01", CLUSTER, "Dell Inc.", "PowerEdge R730", 2, 28, CPU_STR, 2.4, 56,
              RAM_GIB * 1024 * 1024, 12] for h in HOSTS]
    _ws(wb, "ESX Hosts", ["Host-Name", "Datacenter", "vCenter", "Cluster", "Manufacturer", "Model", "CPU Sockets",
                          "CPU Cores", "CPU Description", "CPU Clock Speed (GHz)", "CPU Threads", "Memory (KiB)",
                          "Guest VM Count"], hosts)
    perf = []
    for i, h in enumerate(HOSTS):
        pk, av = PEAK_PCT[i], AVG_PCT[i]
        perf.append([h, pk, round(67.2 * pk / 100, 2), av, round(67.2 * av / 100, 2), 70, 367001, 60, 2500, 1200,
                     "70/30", 180, 90, 1.2, 2.5])
    _ws(wb, "ESX Performance", ["Host-Name", "Peak CPU %", "Peak CPU (GHz)", "Average CPU %", "Average CPU (GHz)",
                                "Peak Memory %", "Peak Memory (MiB)", "Average Memory %", "Peak IOPS",
                                "Average IOPS", "Average Read/Write Ratio", "Peak Throughput MB/s",
                                "Avg. Throughput MB/s", "Avg Read Latency (ms)", "Peak Read Latency (ms)"], perf)
    vmrows = [[v["name"], True, "poweredOn", v["vcpu"], v["mem_gib"] * 1024, int(v["mem_gib"] * 1024 * 0.3),
               int(v["mem_gib"] * 1024 * 0.8), v["prov_gib"] * 1024, v["used_gib"] * 1024, v["prov_gib"] * 1024,
               v["used_gib"] * 1024, False, CLUSTER, v["host"], "DS01", v["os"]] for v in vms]
    _ws(wb, "VMs", ["VM Name", "IsRunning", "PowerState", "Virtual CPU", "Provisioned Memory (MiB)",
                    "Used Memory (active) (MiB)", "Consumed Memory (MiB)", "Guest VM Disk Capacity (MiB)",
                    "Guest VM Disk Used (MiB)", "Virtual Disk Size (MiB)", "Virtual Disk Used (MiB)", "Template",
                    "Cluster", "Host", "Datastore", "Guest OS"], vmrows)
    vperf = [[v["name"], round(v["peak_ghz"] / (v["vcpu"] * 2.4) * 100 * v["vcpu"], 1), v["peak_ghz"],
              round(v["avg_ghz"] / 2.4 * 100, 1), v["avg_ghz"], int(v["mem_gib"] * 1024 * 0.8), 200, 60, 140, 60, 12]
             for v in vms]
    _ws(wb, "VM Performance", ["VM Name", "Peak vCPU %", "Peak vCPU (GHz)", "Average vCPU %",
                               "Average vCPU (GHz)", "Peak Memory (MiB)", "Peak IOPS", "Average IOPS",
                               "Peak Read IOPS", "Peak Write IOPS", "Peak Throughput MB/s"], vperf)
    p = out / "liveoptics_estate.xlsx"
    wb.save(p)
    return p


def gen_timeseries(out: Path) -> Path:
    p = out / "liveoptics_timeseries.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "host", "cpu_pct"])
        for t in range(288):  # 1 day at 5 min
            diurnal = 0.5 + 0.5 * (1 if 96 <= t <= 216 else 0)
            for i, h in enumerate(HOSTS):
                val = AVG_PCT[i] * (0.6 + 0.6 * diurnal) + ((t * 7 + i * 13) % 11) - 5
                w.writerow([f"2026-10-01T{t // 12:02d}:{(t % 12) * 5:02d}:00", h, round(max(1.0, val), 1)])
    return p


def gen_manual(out: Path, lang: str = "en") -> Path:
    from refreshsizer.ingest.manual_template import FR, HOST_FIELDS, STORAGE_FIELDS, WORKLOAD_FIELDS

    def hdr(fields: list[str]) -> list[str]:
        return [FR.get(f, f) if lang == "fr" else f for f in fields]

    wb = Workbook()
    wb.remove(wb.active)  # type: ignore[arg-type]
    hosts = []
    for i in range(4):
        hosts.append([f"hv{i + 1:02d}", "Manual-Cluster", "Paris", "Dell", "PowerEdge R640", "Intel Xeon Gold 6230",
                      2, 20, 2, 2.1, 768, 48 + i, 70 + i, 30, 500, None, "x86", "vSphere", None])
    _ws(wb, "Hosts" if lang == "en" else "Hotes", hdr(HOST_FIELDS), hosts)
    wls = []
    for i in range(40):
        wls.append([f"wl{i:03d}", f"hv{i % 4 + 1:02d}", f"app{i:03d}",
                    "oltp_db" if i < 4 else "general_virt", 8 if i < 4 else 4, 64 if i < 4 else 24,
                    32 if i < 4 else 10, None, None, 800, 1200, 1500 if i < 4 else 100, 70, 40, 5,
                    "sql_ent" if i < 4 else "windows_dc", None, "tier1" if i < 4 else "tier2"])
    _ws(wb, "Workloads" if lang == "en" else "Charges", hdr(WORKLOAD_FIELDS), wls)
    _ws(wb, "Storage" if lang == "en" else "Stockage", hdr(STORAGE_FIELDS), [["Unity01", 120, 60, 2.1, 12000, 600]])
    _ws(wb, "Policy" if lang == "en" else "Politique", ["key", "value"], [["horizon_years", 5]])
    p = out / f"manual_template_{lang}.xlsx"
    wb.save(p)
    return p


def gen_nmon(out: Path) -> Path:
    p = out / "aix_nmon_summary.csv"
    rows = [
        ["ora1", "Power S824", "S824-A", 24, 3.5, 8, 8.0, 10, 6.0, 7.5, 256, 9000, "oltp_db", "oracle_ee"],
        ["ora2", "Power S824", "S824-A", 24, 3.5, 8, 5.0, 8, 4.0, 5.2, 128, 4000, "oltp_db", "oracle_ee"],
        ["ora3", "Power S924", "S924-B", 24, 3.4, 8, 6.0, 8, 5.0, 6.1, 192, 7000, "oltp_db", "oracle_ee"],
        ["ora4", "Power S924", "S924-B", 24, 3.4, 8, 4.0, 6, 3.0, 4.0, 128, 3000, "oltp_db", "oracle_ee"],
    ]
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["lpar", "system_model", "system", "system_cores", "ghz", "smt", "ec", "vp", "physc_p95",
                    "physc_max", "mem_inuse_gb", "iops_p95", "workload_class", "licensing"])
        w.writerows(rows)
    return p


def gen_awr(out: Path) -> Path:
    p = out / "oracle_awr_summary.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["db", "host", "db_cpu_s_per_s_p95", "aas_p95", "host_cpu_pct_p95", "logical_reads_s",
                    "physical_reads_s", "redo_mb_s", "cpu_model", "host_cores", "sga_gb", "db_size_gib"])
        w.writerow(["ERP01", "dbhost01", 9.5, 14.2, 38, 450000, 12000, 6.5, "Intel Xeon Gold 6230", 40, 256, 4096])
        w.writerow(["CRM01", "dbhost02", 6.0, 8.1, 27, 210000, 6000, 3.1, "Intel Xeon Gold 6230", 40, 128, 2048])
    return p


def gen_messy_fr(out: Path) -> Path:
    """French headers, merged 2-row header, subtotal rows, composite CPU strings, separate host/VM sheets
    with mismatched names, and a transposed summary table."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Serveurs"
    ws["A1"] = "Inventaire serveurs - client X"
    # two-row merged header starting row 3
    ws.merge_cells("A3:A4")
    ws["A3"] = "Nom serveur"
    ws.merge_cells("B3:B4")
    ws["B3"] = "Cluster"
    ws.merge_cells("C3:D3")
    ws["C3"] = "Processeur"
    ws["C4"] = "Type CPU"
    ws["D4"] = "Fréquence (GHz)"
    ws.merge_cells("E3:E4")
    ws["E3"] = "Mémoire (Go)"
    ws.merge_cells("F3:G3")
    ws["F3"] = "Charge CPU"
    ws["F4"] = "Pic CPU %"
    ws["G4"] = "CPU moyen %"
    ws.merge_cells("H3:H4")
    ws["H3"] = "Modèle"
    for c in ("A3", "B3", "C3", "E3", "F3", "H3", "C4", "D4", "F4", "G4"):
        ws[c].font = Font(bold=True)
    data = [
        ["SRV-ESX01", "Cluster-Paris", "2x Xeon Gold 6230 (20c)", "2,1", "768 Go", "62%", "35%", "PowerEdge R640"],
        ["SRV-ESX02", "Cluster-Paris", "2x Xeon Gold 6230 (20c)", "2,1", "768 Go", "58%", "31%", "PowerEdge R640"],
        ["SRV-ESX03", "Cluster-Paris", "2x Xeon Gold 6230 (20c)", "2,1", "768 Go", "66%", "38%", "PowerEdge R640"],
        ["Sous-total Paris", None, None, None, "2304", None, None, None],
        ["SRV-ESX04", "Cluster-Lyon", "2 x Intel Xeon E5-2680 v4 (14 coeurs)", "2,4", "512 Go", "0,48", "0,25",
         "ProLiant DL380 Gen9"],
        ["SRV-ESX05", "Cluster-Lyon", "2 x Intel Xeon E5-2680 v4 (14 coeurs)", "2,4", "512 Go", "0,52", "0,27",
         "ProLiant DL380 Gen9"],
        ["Total", None, None, None, "3328", None, None, None],
    ]
    for i, r in enumerate(data):
        for j, v in enumerate(r):
            ws.cell(row=5 + i, column=1 + j, value=v)
    ws2 = wb.create_sheet("Machines virtuelles")
    ws2.append(["Nom VM", "Hôte", "Nb vCPU", "RAM (Go)", "Disque utilisé (Go)", "Disque provisionné (Go)", "Système"])
    for i in range(30):
        host = ["srv-esx01.client.fr", "srv-esx02.client.fr", "srv-esx03.client.fr", "SRV_ESX04", "srv-esx05"][i % 5]
        ws2.append([f"VM-{i:03d}", host, 4 if i % 2 else 8, 16 if i % 2 else 32, f"{200 + i * 10},5", 500,
                    "Windows Server 2016" if i % 3 == 0 else "RHEL 7"])
    ws2.append(["Total", None, 180, 720, None, None, None])
    ws3 = wb.create_sheet("Synthese")
    for k, v in [("Nombre de serveurs", 5), ("Nombre de VM", 30), ("Mémoire totale (Go)", 3328),
                 ("Coeurs totaux", 176), ("Stockage utilisé (To)", 8.5)]:
        ws3.append([k, v])
    ws4 = wb.create_sheet("Notes")
    ws4["A1"] = "Ce fichier a été exporté manuellement."
    p = out / "messy_customer_fr.xlsx"
    wb.save(p)
    return p


def gen_messy_csv(out: Path) -> Path:
    p = out / "messy_semicolon.csv"
    lines = ["Serveur;Processeur;Sockets;Coeurs par socket;Fréquence GHz;RAM (Go);CPU p95 %;Cluster",
             "lnx-db01;Intel Xeon Gold 6334;2;8;3,6;384;41,5;Cluster-DB",
             "lnx-db02;Intel Xeon Gold 6334;2;8;3,6;384;37,2;Cluster-DB",
             "lnx-app01;Intel Xeon Gold 6426Y;2;16;2,5;512;55,0;Cluster-DB"]
    p.write_bytes(("\r\n".join(lines) + "\r\n").encode("cp1252"))
    return p


def gen_model_only(out: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Inventory"
    ws.append(["Hostname", "Server Model", "RAM GB", "Site", "VM count"])
    for i in range(6):
        ws.append([f"legacy{i:02d}", "ProLiant DL380 Gen9" if i < 3 else "PowerEdge R730", 384, "Lille", 15])
    p = out / "model_only_inventory.xlsx"
    wb.save(p)
    return p


def generate_all(out: Path) -> dict[str, Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    return {
        "rvtools": gen_rvtools(out), "liveoptics": gen_liveoptics(out), "timeseries": gen_timeseries(out),
        "manual_en": gen_manual(out, "en"), "manual_fr": gen_manual(out, "fr"), "nmon": gen_nmon(out),
        "awr": gen_awr(out), "messy_fr": gen_messy_fr(out), "messy_csv": gen_messy_csv(out),
        "model_only": gen_model_only(out),
    }


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "fixtures"
    for k, v in generate_all(target).items():
        print(f"{k:12s} {v}")
