# RefreshSizer

Sizes latest-generation x86 servers (Dell PowerEdge 17G: R670, R770, R770AP, R470, R570, R6715, R7715, R6725,
R7725) to replace legacy x86 or RISC (IBM POWER, SPARC) servers, from **performance-normalized demand** –
published SPECrate2017_int_base results per core – never from raw core counts or GHz. Every number in the report
shows its formula, inputs and source, next to the naive core-match and GHz-match results.

## Install (Windows 10/11, no admin rights)

Everything installs into a `.venv` folder inside the project. No system packages, services or registry changes.

1. Install Python ≥ 3.11 for your user only (python.org installer, *"Install for me only"*; or the Microsoft
   Store Python). No admin rights needed.
2. Unzip/clone this folder anywhere in your profile (e.g. `C:\Users\you\RefreshSizer`).
3. Double-click or run `run.bat` once: it creates `.venv`, installs the pinned `requirements.lock` and the tool.
   Behind a corporate proxy, set `HTTPS_PROXY` / `HTTP_PROXY` first (`set HTTPS_PROXY=http://proxy:8080`).
4. Use it: `run.bat run inventory.xlsx --out reports --yes`.

Linux/macOS: `./run.sh ...` (same behaviour). Manual alternative:
`python -m venv .venv && .venv/Scripts/pip install -r requirements.lock && .venv/Scripts/pip install --no-deps -e .`

Everything works **offline**: benchmarks come from the curated database shipped in the repo; the LLM is optional.

## Quick start

```
refreshsizer template --out template.xlsx [--lang fr]          # manual input template
refreshsizer ingest <files...> --out project.json [--yes | --strict] [--no-llm]
refreshsizer size project.json [--policy policy.yaml] [--growth 15] [--years 5] [--objective min_tco] [--p95-estimate]
refreshsizer report project.json --format html,pdf,xlsx --out reports/ [--lang fr]
refreshsizer run <files...> --out reports/ [--yes]             # ingest + size + report in one go
refreshsizer bench update [--suite cpu2017 --since 2024q1] [--offline] [--force]
refreshsizer bench show "EPYC 9355"
refreshsizer ui                                                # local web UI on http://127.0.0.1:8765
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
