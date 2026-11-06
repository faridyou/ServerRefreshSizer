# RefreshSizer

Sizes latest-generation x86 servers (Dell PowerEdge 17G: R670, R770, R770AP, R470, R570, R6715, R7715, R6725,
R7725) to replace legacy x86 or RISC (IBM POWER, SPARC) servers, from **performance-normalized demand** –
published SPECrate2017_int_base results per core – never from raw core counts or GHz. Every number in the report
shows its formula, inputs and source, next to the naive core-match and GHz-match results.

## Install and start (Windows 10/11, no admin rights)

Everything installs into a `.venv` folder inside the project. No system packages, services, registry changes,
Node.js or frontend build step.

1. Install Python ≥ 3.11 for your user only (python.org installer, *"Install for me only"*, or the Microsoft
   Store Python). No admin rights needed.
2. Unzip/clone this folder anywhere in your profile (e.g. `C:\Users\you\RefreshSizer`).
3. **Double-click `run.bat`.** The first time it creates `.venv` and installs the pinned `requirements.lock`
   (it re-installs automatically whenever `requirements.lock` changes). It then starts the web UI on
   `http://127.0.0.1:8765` (or the next free port) and opens your default browser. Close the console window
   (or press Ctrl+C) to stop it.
   Behind a corporate proxy, set `HTTPS_PROXY` / `HTTP_PROXY` before the first run so pip can download packages.

Linux/macOS: `./run.sh` (same behaviour). From a terminal: `refreshsizer ui [--port 8765] [--no-browser]`.
`run.bat <command>` / `./run.sh <command>` run the CLI instead (see below).

Everything works **offline**: HTMX is vendored in `refreshsizer/ui/static/vendor/`, benchmarks come from the
curated database shipped in the repo, and the LLM is optional. The UI binds to **127.0.0.1 only** and has no
authentication; a banner on every page reminds you that it is a local application.

## Web UI (primary interface)

Each sizing is a **project** stored in `data/projects/<name>/` (input files, `project.json`, `policy.yaml`,
mapping decisions in `mappings/` and `mapping_review.json`, the last 5 sizing runs in `runs/`, and `reports/`).
Projects persist between sessions. Long tasks (ingest, sizing with Monte Carlo, reports, benchmark update) run as
background jobs with a progress bar. The EN/FR toggle (top right) switches every label; strings live in
`refreshsizer/report/i18n.yaml`. The UI calls the same functions as the CLI (`refreshsizer/workflow.py`), so the
numbers are identical.

What you see, page by page:

* **Projects (home)** – a table of projects (name, last update, number of files and runs) with *Open*,
  *Duplicate* and *Delete* (with a confirmation dialog), and a *New project* box at the top.
* **1 Upload** – a dashed drop zone: drag several xlsx/xls/xlsm/ods/csv files onto it (or click to choose). Each
  file is listed with its detected format as a coloured tag (RVTools, Live Optics, RefreshSizer template, AIX nmon,
  Oracle AWR, CPU time series, universal) and a *Remove* button. Buttons download the blank manual template in
  English or French. *Run ingest* starts the ingest job.
* **2 Mapping** (universal ingest) – an editable grid: file, sheet, source column, a dropdown for the canonical
  field (with the originally proposed field shown underneath when you change it), a unit dropdown, the confidence
  as a green/amber/red pill, the sample value and the transformed sample, and an *ignore* checkbox. A filter box and
  sortable headers help on large files. *Accept all ≥ threshold* keeps every proposal at or above the threshold
  and ignores the rest; *Apply and re-ingest* re-runs the ingest with your decisions; *Save as mapping profile* also
  stores them globally so the same customer template is recognised automatically next time. Ignored sheets are
  listed with the reason.
* **3 Ingest summary** – estate totals per cluster and overall (hosts, sockets, cores, RAM, VMs, vCPU, vRAM,
  provisioned/used storage, IOPS), each CPU model with its matched benchmark, sizing mode and confidence per
  cluster, data-quality flags, assumptions, transformations, and the customer question list in English and French,
  each with a *Copy* button. Below it, the **Target servers** selector: pick *Intel and AMD*, *Intel Xeon 6* or
  *AMD EPYC 9005*, tick the Dell platforms the customer accepts (grouped by CPU vendor, with height in U, sockets
  and number of supported CPUs), and optionally restrict to specific CPU models. *Run sizing* saves this choice in
  the project's `policy.yaml`.
* **4 Policy** – a form generated from the configuration schema, grouped as growth and horizon, U_max per class,
  HA, memory, storage (protection, slack, DRR), vCPU ratios/NUMA, licensing, optimizer objective, utilization
  handling, inventory-only mode, RISC factors and Monte Carlo. Each field shows inline help and its default; changed
  fields are highlighted. *Save* writes only the differences to `policy.yaml`; *Reset to defaults* removes it.
* **5 Results** – summary cards (recommended configuration, hosts, cores, SU vs C_req, headroom / failure-mode
  utilization, licensing delta, P50/P90, TCO), each with **Show calculation** expanders that reveal the formula,
  inputs, output, sources with clickable URLs and assumptions. Below: the bar chart of performance-normalized vs
  core-match vs GHz-match hosts, the Monte Carlo histogram, the Pareto chart, a sortable and filterable candidate
  table (every candidate row also expands to its calculations), the **CPU scenarios** table – the best
  configuration for every CPU model allowed by the target selection, tagged *recommended*, *best Intel* and *best
  AMD*, with SPEC basis (published or estimated), hosts, cores, failure-mode utilization, power, TCO and the
  difference to the recommendation; *Use this scenario* re-runs the sizing on that platform and CPU so its BoM and
  reports can be produced for a customer who will not consider the other vendor – the per-host BoM with the DIMM channel diagram,
  constraint checks with OK/WARN/FAIL tags, the uncertainty section (tornado, or the 100/70/50 % table in
  inventory-only mode) and the benchmark basis.
* **6 What-if** – change CPU growth, horizon, U_max (all classes), the objective or the target servers (one-off,
  the saved selection is not changed) and re-run in place. The last
  5 runs are kept with their label and overrides; pick two (A/B) to see them side by side (D0, D_T, C_req,
  recommendation, hosts, P50/P90, naive counts, TCO, licensing delta).
* **7 Reports** – generate HTML, Excel (live formulas) and PDF in English or French for any kept run; download
  them, or open the HTML report in a new tab.
* **Benchmarks** – search the benchmark DB (CPU, metric, base value, cores, chips, SPC, system, live/curated/user
  source, URL) and see the resolved SPC with its method and confidence band; *Update from spec.org* runs in the
  background and says clearly when spec.org is unreachable and the curated offline data is kept; a form adds a
  user-provided SPECrate2017_int_base value, which then takes precedence for that CPU.

## Command line (still fully supported)

```
refreshsizer template --out template.xlsx [--lang fr]          # manual input template
refreshsizer ingest <files...> --out project.json [--yes | --strict] [--no-llm]
refreshsizer size project.json [--policy policy.yaml] [--growth 15] [--years 5] [--objective min_tco] [--p95-estimate]
                  [--cpu-vendor intel|amd] [--platforms R7725,R770] [--cpus "EPYC 9355,Xeon 6787P"]
refreshsizer report project.json --format html,pdf,xlsx --out reports/ [--lang fr]
refreshsizer run <files...> --out reports/ [--yes]             # ingest + size + report in one go
refreshsizer bench update [--suite cpu2017 --since 2024q1] [--offline] [--force]
refreshsizer bench show "EPYC 9355"
refreshsizer ui [--port 8765] [--no-browser]                   # local web UI (127.0.0.1 only)
```

Sample inputs: `python tests/fixtures_gen.py` writes them to `tests/fixtures/` (already committed).

### Inputs

| Format | Detection | Utilization → mode |
|---|---|---|
| RVTools `.xlsx` (vInfo, vHost, vCPU, vMemory, vDisk, vPartition) | sheet names | snapshot → SNAPSHOT (× 1.4) |
| Dell Live Optics (VMware Excel / General Excel), optional time-series CSV `timestamp,host,cpu_pct` | sheet names | peak → SUMMARY; time series → MEASURED |
| RefreshSizer manual template (EN/FR) | Hosts/Workloads sheets | p95/peak → SUMMARY |
| AIX nmon/lparstat summary CSV | `lpar,physc_p95,smt` | physc p95 → SUMMARY |
| Oracle AWR summary CSV | `db,db_cpu_s_per_s_p95` | DB CPU p95 → SUMMARY |
| **Any other** xlsx/xls/xlsm/ods/csv | universal ingest | whatever it contains; none → INVENTORY_ONLY |

Universal ingest profiles every sheet (multi-row/merged headers, several tables, transposed tables, subtotal and
note rows, units such as `512 Go`, `2,4`, `2.4-3.3 GHz`, `2x Xeon Gold 6230 (20c)`, `;`/cp1252 CSVs), maps columns
with an EN/FR synonym dictionary (`config/synonyms.yaml`) and value heuristics, and – only if `ANTHROPIC_API_KEY` is
set – asks the LLM about unresolved columns. **The LLM only interprets structure; it never computes sizing values.**
Hostnames, IPs, FQDNs and customer names are redacted before anything is sent. Mappings are reviewed
(interactive / `--yes` ≥ 0.6 / `--strict` ≥ 0.9) and saved as profiles in `data/mapping_profiles/`, so the same
customer template is recognized next time. The ingest summary ends with a ready-to-paste list of customer questions.

## Methodology (summary)

1. **Source capacity** SU_cap(h) = SPC(cpu) × cores(h), SPC = SPECrate2017_int_base ÷ enabled cores.
   POWER: rPerf/core(SMT) × cores × F (F = 0.425 conservative; S924 anchor 0.365, E1080 0.425).
   Missing CPU → other socket count (scaled), sibling SKU × clock ratio (ESTIMATE), user value, curated,
   generation average (LOW).
2. **Mode per cluster** MEASURED (time-aligned cluster p95) / SUMMARY (Σ host p95, else peak) /
   SNAPSHOT (× 1.4) / INVENTORY_ONLY (min(Σ assumed util × SU_cap, Σ vCPU × SPC)). Saturation > 80 % → × 1.10.
3. **Adjustments** D0' = D0 × k_arch × k_virt × k_wl (RISC→x86: OLTP 1.25, SAP 1.15, other 1.10; bare metal →
   virtual 1.07).
4. **Growth** D_T = D0' × (1+g)^T (+ step events). **Headroom** C_req = D_T ÷ U_max(class).
5. **HA** N_active = ⌈C_req ÷ SU_host⌉, + N+1 (N+2 above 16 hosts); vSAN RAID-5 4+1 minimum 4 hosts; failure-mode
   utilization ≤ U_max.
6. **Memory** M_T = M0 (1+g_m)^T (1+0.05) ÷ (1−0.05); balanced DIMMs, 1 DPC preferred.
   **Storage** Raw = Used0 (1+g_s)^T ÷ DRR × P_prot ÷ (1−slack). **Network** 2 × 100 GbE for vSAN ESA all-NVMe.
7. **Optimizer** exhaustive over platform × CPU × DIMM × drives; TCO = capex + power + licensing (VMware VCF/VVF,
   Windows DC, SQL Server, Oracle, RHEL); top 5 + Pareto front; per-core-licensed databases split into their
   own cluster optimized on SPC.
8. **Uncertainty** Monte Carlo 5,000 runs (seed 42) → P50/P90 hosts (recommend P90) + tornado.

Defaults: `refreshsizer/config/defaults.yaml` (override with the template's Policy sheet, `--policy policy.yaml`
or CLI flags). Decisions not covered by the specification: see [DECISIONS.md](DECISIONS.md).

## Reports

`reports/refreshsizer_report.html` (self-contained, primary), `.pdf` (xhtml2pdf; if it fails, the HTML is kept and
a warning logged) and `.xlsx` (one sheet per section, **live formulas**: change a yellow input cell and the host
count, utilization, memory, storage, naive comparisons and licensing recompute). Sections: executive summary,
input data & confidence, source inventory, benchmark basis, normalization math, growth & headroom, candidates,
BoM with DIMM diagram, memory/storage/network, licensing, constraints, uncertainty, validation plan, appendix.
`--lang fr` gives French (labels in `report/i18n.yaml`).

## Updating catalogs and benchmark data

* **SPEC results**: `refreshsizer bench update` crawls spec.org quarterly index pages (newest first, incremental,
  ≤ 1 request/s, 20 s timeout, 2 retries, ETag/Last-Modified cache, at most weekly unless `--force`; honours
  `HTTP(S)_PROXY`). Live results override curated rows for the same system. Offline, nothing changes.
  CPU2026 results are stored but never compared numerically with CPU2017.
* **Curated seeds**: `refreshsizer/benchmarks/seeds/spec_cpu2017.yaml` (results + generation averages) and
  `workload_benchmarks.yaml` (SAP SD, VMmark 4, ...). The SQLite DB is rebuilt automatically when a seed changes.
* **CPU specs**: `catalog/cpu_specs.yaml`. **Dell platforms**: `catalog/dell_17g.yaml` (supported CPUs, DIMM sizes,
  speeds by DPC, bays, PSUs, base power). **The Dell spec sheet / configurator is authoritative** – the catalog is
  a seed. **IBM rPerf**: `catalog/ibm_rperf.yaml`. **Oracle core factors**: `catalog/oracle_core_factor.yaml`.
  **Legacy server → CPU map**: `catalog/legacy_server_cpus.yaml`. **Prices** (placeholders): `config/prices.yaml`.

## Data sources and terms of use

* SPEC®, SPECrate® and SPEC CPU® are trademarks of the Standard Performance Evaluation Corporation. Results are
  cited with URL, base metric, sponsor, system and dates per SPEC fair-use rules; **comparisons are RefreshSizer's
  estimates, not SPEC's**. Only base metrics are used.
* IBM rPerf/CPW are IBM estimates (IBM Power performance report). Oracle core factors from Oracle's Processor Core
  Factor Table. SAP SD and VMmark results from the respective publishers. Intel ARK / AMD product pages for CPU
  specifications – respect each site's terms; the refresh helpers fetch politely and fall back to the files above.
* Licensing rules are simplified models; VMware's reported 72-core order minimum (2025) is disputed – confirm with
  Broadcom/your distributor. Prices are placeholders, not quotes.
* Customer data never leaves the machine, except redacted headers/samples sent to the LLM when it is enabled.

## Development

```
.venv/bin/python -m pytest          # all tests run offline (LLM mocked, HTTP recorded)
.venv/bin/ruff check refreshsizer tests
.venv/bin/mypy refreshsizer tests
```

Non-goals: price quotes, GPU/AI-training sizing (placeholder), mainframe MIPS (flagged "PoC required").
