# DECISIONS.md

Decisions taken during the build that were not fully covered by the Decision Log of the build prompt.
Each entry: **decision** – reason – how to change it.

## Environment / data

1. **spec.org was unreachable from the build environment (proxy 403).** The curated offline database
   (`refreshsizer/benchmarks/seeds/*.yaml`, built into `data/benchmarks.sqlite`) is used; the crawler is
   implemented and tested against recorded HTTP fixtures (`tests/fixtures/http/`).
   – Network policy. – Run `refreshsizer bench update` on a machine that can reach spec.org.
2. **Curated rows carry `"spec.org (curated, URL to be refreshed)"`** instead of a result URL, and retrieval
   date `2026-01-01 (curated seed)`. A live result for the same CPU/system/chip count supersedes the curated row.
   – URLs could not be verified offline. – Add `url:` to the seed rows or run `bench update`.
3. **CPU spec catalogue values and all prices are best-effort placeholders** (cores, clocks, TDP, L3, 1k-unit
   list prices). – No vendor access during the build. – Edit `catalog/cpu_specs.yaml`, `config/prices.yaml`.
4. **Project data lives in `<repo>/data/`**; override with `REFRESHSIZER_HOME` (or `REFRESHSIZER_BENCH_DB`).
   – User-space only, no admin rights. – Set the environment variable.
5. **Built and tested on Python 3.13** (the newest available); code stays 3.11-compatible (ruff `py311` target,
   no 3.12-only syntax). – Detected interpreter. – Any Python ≥ 3.11 works.

## Golden-test interpretations

6. **Golden A uses Live Optics host *Peak* CPU % (average 55 % across hosts).** The VMware Excel has no p95 column,
   and SUMMARY mode without a time series uses the peak (conservative), so the fixture's peaks average 55 %.
   – Matches "SUMMARY mode with p95 = 55 %". – Supply a time series (MEASURED) or `--p95-estimate`.
7. **Naive core match divides by the target host's physical cores (2 × EPYC 9355 = 64 cores)**:
   280 × 1.15⁵ / 0.75 / 64 → 12 + 1 = 13 hosts; GHz match 369.6 GHz × 1.15⁵ / 0.75 / (64 × 3.55) → 5 + 1 = 6.
8. **Drive choice = cheapest $/TB set that fits the bays, minimum 4 drives per host, tie → fewest drives**
   (prices.yaml has a $/TB sweet spot at 7.68 TB). Pure "fewest drives" would pick 2 × 30.72 TB, which is poor
   for vSAN ESA rebuilds/performance; golden A expects 8 × 7.68 TB.
   – Reproduces the golden value with a realistic rule. – Edit `drive_usd_per_tb` / `min_drives_per_host`.
9. **rPerf → SPEC factor defaults to the conservative 0.425 for every POWER generation** (golden B uses it for S924
   too); the generation-matched value (POWER9 0.365, Power10 0.425) is always recorded in the report.
   – Golden B. – `rperf_factor_mode: generation_matched`.
10. **Golden B is run with CPU growth 10 %/yr** (policy override in the test), per its formula (× 1.1⁵).
    Default growth stays 15 %.
11. **Clusters sourced from RISC platforms, or made only of bare-metal hosts, default to external storage** – vSAN
    minimum host counts do not apply (minimum 2 hosts for HA). Golden B therefore gives 2 hosts.
    – A PowerVM/Oracle estate normally keeps a SAN. – `storage_architecture: vsan_esa | external | auto`.
12. **Per-core-licensed DB clusters license Oracle on active nodes only** (failover node, 10-day rule); both
    figures are always reported (golden B: 16 active / 32 all nodes).
    – Golden B. – `oracle_db_cluster_licensing: all_nodes` (required under VMware without hard partitioning).
13. **DB-cluster ranking ("optimized on SPC")**: fewest licensed DB cores → measured SPEC first → highest SPC →
    fewest hosts → SPEC measured on that exact platform → TCO. Picks R7725 2 × EPYC 9175F for golden B.

## Engine

14. **`prefer_measured_spec: true`** – configs whose SPC comes from a published result for that CPU and socket
    count rank ahead of sibling/scaled estimates (otherwise VMware's 16-core-per-CPU minimum makes small,
    un-benchmarked CPUs win on TCO). The Pareto front is computed on the same pool. – Set it to `false`.
15. **k_HA = 2 when N_active + 1 > 16** ("more than 16 hosts per cluster").
16. **k_virt is weighted by the share of source capacity running bare metal** (1.07 if all bare metal).
17. **k_wl = (workload-metric ratio src/tgt) ÷ (SPC ratio src/tgt)** per core, so a target that does better on the
    workload benchmark than SPEC suggests reduces demand. Only applied when both sides have the benchmark.
18. **Average-only host utilization → average × 1.4 (burst factor), flagged ESTIMATE.**
19. **Demand path priority inside SUMMARY mode**: LPAR physc → AWR DB CPU → host p95/peak → VM GHz (× 0.7 coincidence
    when peaks are used).
20. **Inventory-only storage uses provisioned capacity**; with no data at all the storage figure is 0 and a customer
    question is raised.
21. **vCPU:pCore check grows vCPUs with CPU growth**; a performance-normalized ratio above R_max adds hosts; raw above
    but normalized below R_max is accepted with a pilot warning.
22. **NUMA (largest VM ≤ cores per NUMA node) is a hard constraint for x86 VMs only**; POWER virtual processors are
    not x86 vCPUs, so LPARs are skipped.
23. **DB split share for host-level demand = vCPU share** of the licensed workloads (when no workload-level CPU data).
24. **Memory: "GB" means GiB everywhere** (DIMMs are binary); universal ingest treats GB/Go as GiB and TB/To as TiB.
25. **TCO = capex + power opex (avg load 60 %, PUE 1.5, $0.15/kWh) + licensing over the horizon.** – `prices.yaml`.
26. **Monte Carlo** uses P50/P90 by inverted CDF; inventory-only clusters sample assumed utilization 50–100 %.
    Memory/storage-driven host counts act as a floor.
27. **CPU assumed from a server model** = newest supported generation in `legacy_server_cpus.yaml`, its most common
    core count, the highest-SU SKU of that count, 2 sockets – flagged ASSUMPTION + customer question.
28. **Source licensing** for the delta: VMware cores "if renewed on source"; Oracle on POWER = LPAR virtual
    processors × 1.0 (PowerVM hard partitioning), support only.

## Ingest / LLM / UI / reports

29. **LLM call = `client.messages.parse(..., output_format=LlmResponse)`** (structured output validated by pydantic),
    `stop_reason == "refusal"` handled, one retry on invalid output, any error → rules-only. Model from config
    (`claude-sonnet-5-5`). Tests inject a mock callable; no real API calls.
30. **Interactive review falls back to `--yes` behaviour when stdin is not a terminal.**
31. **Mapping profiles are saved automatically** for every universal table with accepted mappings (fingerprint =
    sorted normalized headers). Delete `data/mapping_profiles/*.json` to forget them.
32. **Reconciliation tolerance 2 %**; on a gap the recomputed value is used and the conflict is listed.
33. **HTML charts are static PNGs** (matplotlib, per the Decision Log) – identical in HTML, PDF and Excel.
    MathJax loads from a CDN; every formula is also shown as plain text, so offline viewing loses nothing.
34. **Excel live formulas** reproduce D0 (host-level paths), D0', D_T, C_req, SU_host, N_active, k_HA, memory- and
    storage-driven host counts, N_total, failure-mode utilization, naive comparisons, M_T, raw storage and
    licensing totals. Each formula carries the engine result as cached value. The vCPU-ratio iteration is not in
    the formula (noted on the sheet).
35. **Web UI uses plain HTML forms with HTMX as progressive enhancement** (works if unpkg is blocked); binds to
    localhost only; sessions under `data/ui_sessions/`.
36. **`refreshsizer ingest` exits with code 2 on blocking validation errors of the manual template** and writes
    `<out>.validation.txt`; other formats only warn.

## Web UI as the primary interface (change request 1)

37. **One shared entry-point module, `refreshsizer/workflow.py`** (ingest, size, reports, policy assembly). The CLI
    was refactored onto it and the UI calls only it, so there is no duplicated logic; tests assert that golden A, B
    and C give byte-identical cluster results (demand, candidates, Monte Carlo, explanations) through both paths.
    – "UI must call the same functions as the CLI". – n/a.
38. **Hooks added to existing functions, none changing a calculation**: optional `progress(fraction, message)`
    callbacks on `ingest_files`, `size_project`/`size_cluster` and `write_reports`; `ingest_files(profiles_dir=...)`
    passed through to universal ingest; `MappingEntry.proposed` (the field proposed before review);
    `apply_user_edits` accepts `(target, unit)` so unit decisions are stored.
39. **Policy precedence in the UI** = CLI: defaults < project Policy sheet < project `policy.yaml` < what-if
    overrides. `policy.yaml` stores only differences from the defaults.
40. **What-if "U_max" sets one value for every workload class**, so it applies whatever the cluster's dominant
    class is. – Simple single knob. – Use the Policy page for per-class values.
41. **Mapping decisions are per project** (`data/projects/<n>/mappings/`, same profile format as
    `data/mapping_profiles/`). Matching global profiles are copied into the project before each ingest; *Save as
    mapping profile* copies the project's profiles back to the global store. `mapping_review.json` keeps the
    first-ingest proposals with their original confidence (re-ingests use the profiles and would otherwise show
    1.0 everywhere) plus the user's decisions. The UI ingest uses the same review mode as `--yes` (≥ 0.6), then the
    grid lets the user change anything.
42. **Background jobs run in their own daemon thread** (`ui/jobs.py`); jobs of the same project are serialized
    by a per-project lock and matplotlib rendering by a lock in `report/charts.py`. (The first version used a
    single worker, so one stuck job blocked every later job – changed after a user report.) Jobs are kept in
    memory (lost on restart – results are already written to the project folder). Progress is polled by HTMX
    every second, shows elapsed time, and warns after 90 s without progress; the finished job answers with
    `HX-Redirect`. Without JavaScript the job page refreshes itself. A running Python thread cannot be killed:
    restarting the UI stops a stuck job. Jobs and errors are logged to `data/logs/ui.log`.
43. **Runs**: the last 5 sizing runs per project are kept in `runs/` (each with its label, overrides and full
    sizing). `project.json` always holds the latest run; reports can be generated for any kept run.
44. **HTMX 1.9.12 is vendored** (`ui/static/vendor/htmx.min.js`, 0BSD licence copied next to it), fetched from the
    npm registry because the CDN was blocked. Charts in the UI are the same matplotlib PNGs as in the reports.
    Sorting/filtering/copy/drag-and-drop are ~60 lines of vanilla JS (`ui/static/app.js`); every page still works
    with plain form posts.
45. **Server**: `127.0.0.1` is hard-coded (`ui/server.py`, no `--host` option); port 8765 or the next free port in
    8765–8814; the browser is opened 1.2 s after start (`--no-browser` to skip). FastAPI's /docs pages are disabled.
46. **Launchers**: `run.bat` / `run.sh` without arguments start the UI; with arguments they run the CLI. They create
    the venv if missing and re-install when `requirements.lock` differs from the copy stored in `.venv` at the last
    install. FastAPI, uvicorn and python-multipart are now core dependencies (were the optional `ui` extra).
47. **Project names** are slugs (`[a-z0-9._-]`, ≤ 64 chars) of the display name; duplicates get `-2`, `-copy`, ...
    Upload accepts only xlsx/xls/xlsm/ods/csv/txt/tsv; file names are reduced to their base name; report downloads
    are confined to the project's `reports/` folder.
48. **User-provided benchmark values** are stored in the benchmark DB with `source=user` (suite cpu2017,
    SPECrate2017_int_base) and take precedence over live and curated results for that CPU (existing SPC rule).
    The CPU is matched to the catalog when possible, otherwise slugified.
49. **UI help texts** are in `report/i18n.yaml` (`h_<field>` keys, EN/FR) next to the other UI strings (`ui_*`);
    fields without a help key show only their default.
50. **Policy ranges are validated by the schema** (`config/schema.py`, `Policy._check_ranges`), so every source
    (defaults, Policy sheet, policy.yaml, CLI, UI) is checked: U_max in (0, 1] (a value like 75 is rejected – it
    is a fraction), growth > -100 %, horizon 1-30 years, slack/free reserve in [0, 1), DRR and vCPU ratios > 0,
    Monte Carlo runs >= 1. A U_max <= 0 previously made the HA loop run for ever (job stuck at 2 % / 50 %); the
    loop now also has a safety bound. Valid policies give exactly the same results as before.

## Target server selection and CPU scenarios (change request 2)

51. **Target selection = three policy keys**: `target_platforms` (existing), `target_cpu_vendors` (Intel / AMD) and
    `target_cpus` (catalog CPU ids); null = no restriction. They only filter the configurations the optimizer
    enumerates – no calculation changes – and are validated against the catalog (unknown names are rejected).
    Sources: Policy sheet, `policy.yaml`, CLI `--platforms / --cpu-vendor / --cpus`, UI selector.
52. **Where the UI selector lives**: ingest summary (saved to `policy.yaml` when you run the sizing – it is the
    customer's choice for the project), Policy page (saved), What-if (one-off override for that run only).
    Picking a vendor ticks that vendor's platforms (every Dell 17G platform is single-vendor).
53. **CPU scenarios** = the best feasible configuration per CPU model, ranked exactly like the recommendation
    (objective, then `prefer_measured_spec`), at most `cpu_scenarios_max` (15) rows, but the best configuration
    of each CPU vendor is always included even beyond the limit, so the "best Intel" and "best AMD" options are
    always visible. Shown in the UI results, the CLI output, the HTML report (section 7) and Excel (sheet 7,
    live TCO delta formulas).
54. **"Use this scenario"** re-runs the sizing restricted to that platform and CPU (a new kept run labelled
    `scenario: ...`), so the BoM, constraint checks, Monte Carlo and reports are produced for it; it does not change
    the saved target selection.
55. **A selection that matches no configuration** (e.g. AMD CPUs on an Intel-only platform) gives a clear warning
    and no recommendation instead of an error; selecting no platform at all is rejected on the form.

## Real Live Optics exports (bug report, Nov 2026)

56. **Live Optics column aliases widened from a real export**: ESX Performance names its host column `Host`
    (ESX Hosts uses `Host Name`), VMs use `Power State` and `VM OS`, `Avg Throughput MB/s` / `Avg IOPS`. The host
    column was not recognised, every utilization row was dropped silently and clusters were sized INVENTORY_ONLY.
    The parser now also warns loudly when the performance sheet has no host or CPU column, or when none of its
    rows matches a host. (The customer workbook is not committed; tests use a synthetic workbook with the same
    headers.)
57. **Collection length**: read from the Summary sheet (`Duration` d.hh:mm:ss, else End - Start). Shorter than
    7 days -> ingest warning, data-quality flag and a customer question (EN/FR) asking for a 7-30 day
    collection. Confidence and numbers are not changed (the sizing still uses the peaks it has).
58. **Raw .xlsx fallback reader** (`ingest/xlsx_raw.py`, stdlib only) for files openpyxl refuses (Live Optics
    AIR writes a stylesheet openpyxl rejects). Values only - no styles, merged cells or formulas; used only
    when openpyxl/pandas fail.
59. **Live Optics AIR (Aging Infrastructure Report)** is detected (`Servers` sheet with `Rule Name` /
    `Rule Details`) and its end-of-support findings are listed as ingest warnings ("Live Optics AIR: <server>:
    <rule> - <details>"); it carries no capacity or utilization data and is not used for sizing.
60. **Powered-off VMs and templates**: the existing `exclude_powered_off` / `exclude_templates` switches are now
    on the Policy page (Utilization handling) - set exclude_powered_off to false when powered-off VMs will be
    migrated and restarted on the new platform.

## Nutanix as target stack (change request 3)

61. **`target_stack`**: vmware_vsan (default, unchanged results), nutanix_ahv, nutanix_esxi. Chosen in the UI
    Target servers box, `policy.yaml` / Policy sheet or CLI `--stack`. Source-side demand is identical; only the
    target side changes.
62. **CVM CPU**: the Controller VM's vCPUs (`nutanix_cvm_vcpu`, default 12) are reserved as **physical cores**
    on every node: SU_usable = SU_host x (cores - CVM) / cores. Conservative (a vCPU could be a hyperthread);
    nodes with fewer cores than the CVM are infeasible, and a warning is shown when the CVM takes > 50 % of a
    node. Default 12 = typical all-NVMe node; Nutanix's documented minimum is 8 - confirm with Nutanix Sizer.
63. **CVM memory**: `nutanix_cvm_memory_gb` (default 48; Nutanix minimum 32, more with dedup/compression or
    large drives) is added to each node's memory requirement before the DIMM choice. The Excel memory host-count
    formula subtracts it from the installed memory.
64. **Storage**: Nutanix is always HCI (DSF) - the external-SAN auto rule for RISC/bare-metal sources does not
    apply. Protection overhead / minimum nodes: RF2 2.0 / 3, RF3 3.0 / 5, RF2 EC-X 4+1 1.25 / 6,
    RF3 EC-X 4+2 1.5 / 7. EC-X only applies to write-cold data, so the EC-X options are optimistic; RF2 is the
    default. vSAN's high-IO 10 % CPU reserve is not applied (the CVM reservation replaces it).
65. **NUMA / vCPU ratio**: the vCPU:pCore check uses the cores left after the CVM; on single-socket nodes the
    largest VM must fit in the socket minus the CVM cores.
66. **Licensing**: AHV -> no VMware; Nutanix NCI (Starter / Pro / Ultimate) per physical core per year,
    placeholder prices in `prices.yaml`, optional minimum cores per CPU (default 0). Nutanix on ESXi -> VMware
    VCF/VVF per core **and** NCI, without the vSAN capacity entitlement check.
67. **Platforms**: the Dell 17G catalog is reused as-is; confirm each node model and CPU on the Nutanix
    hardware compatibility list (or Dell XC) before quoting.

## Report levels (change request 4)

68. **Three levels** - `customer`, `presales`, `full` - for HTML and PDF (separate templates sharing one style
    file); `full` is the original report and stays the CLI default (`--level`), `presales` is the UI default.
    File names: `refreshsizer_report[_customer|_presales][_fr].{html,pdf,xlsx}`.
69. **Customer level shows no internal prices**: no TCO, capex or licence costs (all placeholders from
    prices.yaml) and no formulas, Monte Carlo or policy jargon. It shows the current environment in cores and
    GHz, the SPEC per-core comparison with sources, the proposed configuration with its sizing assumptions in one
    sentence, alternative CPU options (servers, cores, load, power) and plain-language next steps plus the
    customer questions. The "servers needed" chart is relabelled (this proposal / same core count / same GHz).
70. **Excel by level**: customer -> a 4-sheet values-only workbook (summary, current environment, benchmarks,
    proposal); presales and full -> the live-formula workbook (it is the audit trail, already one sheet per
    section).
71. **Presales level** keeps every decision-relevant number but one line per step: the sizing chain
    (SU capacity -> D0 -> D0' -> D_T -> C_req -> SU per host -> hosts, memory, storage), constraint checks only
    when not OK, P50/P90 in one sentence, at most 8 warnings (the rest in the full report).

## Word and PowerPoint export (change request 5)

72. **One outline, two writers**: `report/outline.py` turns the report context (same data as the HTML, per
    level) into neutral sections and blocks (heading, paragraph, KPI tiles, table, image, bullets); `docx_report.py`
    and `pptx_report.py` only lay those out. The HTML/PDF templates are untouched, so no calculation or number
    changes; the levels keep their rules (customer: no prices, no formulas).
73. **Libraries**: `python-docx` and `python-pptx` (pure Python + lxml wheels, no Office needed, install in the venv
    without admin rights, offline once installed). Charts are the same matplotlib PNGs as the HTML report.
74. **Deck layout**: 16:9, blank layout with a title bar (no corporate template is shipped - the deck is meant to
    be pasted into one); one section per slide, content that does not fit continues on a "(cont.)" slide, tables
    at most 12 rows per slide with the header repeated, font size chosen from the column count (16 -> 11 pt).
    The customer intro goes on the title slide, the disclaimer on a final Notes slide. The full level's
    calculation details become monospace lines on several slides - presales is the level meant for decks.
75. **Section numbers** from the HTML report ("8. Recommended bill of materials") are removed in Word/PowerPoint
    titles because sections are regrouped per cluster there.
76. **PDF stays HTML-based** (xhtml2pdf); Word/PowerPoint can be exported to PDF by Office if a styled PDF from
    them is preferred. In the UI both new formats are ticked by default; the CLI default is unchanged
    (`html,pdf,xlsx`).

## Storage architecture and customer email prompt (change request 6)

77. **Architecture choice**: `storage_architecture` = `auto` (unchanged default: HCI, external for RISC/bare-metal
    sources), `hci` or `external` (disaggregated: servers + hypervisor + external array). It is on the target
    selector next to the stack. `vsan_esa` is still accepted as a synonym of `hci`. A top-level `None` override now
    means "back to the default" (also fixes resetting `target_stack` in a one-off sizing).
78. **What disaggregated changes for the servers**: boot drives only (BOSS), no vSAN capacity add-on, the 2-host HA
    minimum instead of the vSAN/Nutanix minimums, no vSAN CPU reserve - the existing `external` protection path.
    The CPU demand (D0, D_T, C_req) is identical; only the host floor and the drives differ.
79. **The array never changes the host count or the compute TCO** used to rank candidates (it is the same for every
    candidate). Its cost (appliances, enclosures, drives, plus one storage adapter set per host) is reported
    separately as "Storage total"; golden results are unchanged.
80. **PowerStore model** (`catalog/powerstore.yaml`): 500T, 1200T, 3200T, 3200Q, 5200T, 9200T; base enclosure slots,
    24-slot expansion enclosures, max drives, up to 4 appliances. Usable = (drives - 1 spare per resiliency set) x
    TB x 0.9095 x DRE parity efficiency (single 4+1 / 8+1, double 4+2 / 16+2 by drive count) x (1 - 8 % system
    overhead) - a deliberately simple model. Requirement = Used0 (1+g_s)^T / DRR / max fill (default 80 %). The
    cheapest layout per model is kept (fewest appliances, then drives); the cheapest model is proposed and the
    others are listed. Array data reduction defaults to the per-class `drr` (conservative 1.3-1.5:1); set
    `external_storage.drr` (e.g. 3) for a PowerStore-typical assumption.
81. **Indicative IOPS are placeholders, not Dell figures** (Dell does not publish per-model IOPS); they only rank
    models relatively and add appliances when demand exceeds 70 % of them. Every report says the array must be
    validated with Dell's PowerStore sizing tools; prices are placeholders in `prices.yaml`.
82. **Connectivity**: two redundant fabrics/networks; `host_ports` (default 2) per host as dual-port 32 Gb FC HBAs
    or dual-port 25 GbE NICs dedicated to storage; array front-end ports 4 per appliance (8 above 16 hosts); switch
    ports per fabric = hosts x ports/2 + array ports/2 (ISLs and spares excluded).
83. **Nutanix + external is refused** with a clear message: Nutanix disaggregated runs on Dell PowerFlex, which is
    not modeled; PowerStore is not a Nutanix storage option.
84. **"Existing array"** keeps the customer's array: requirement and connectivity are reported, no array is sized,
    and the report asks to confirm free capacity, performance, support date and compatibility.
85. **Storage design section** in every report level and format (HTML/PDF, Word, PowerPoint, Excel, UI results)
    from one view: customer = capacity, layout, connectivity (no prices); presales adds storage cost and other
    array models; full adds the formulas. HCI clusters get the same section (drives per host, raw, usable after
    protection, effective, fill).
86. **Email: a prompt, not an AI call**. The tool stays offline and AI-free; it produces a self-contained prompt
    (instructions + facts) to paste into any assistant. The facts come from the customer report context, so the
    email cannot contradict the report; prices are excluded unless requested, low-confidence data adds a "confirm
    before ordering" rule, and the page warns that the prompt contains customer data. Instructions exist in
    English and French (the facts use the report labels of the chosen language).

## Consolidation and unit naming (change request 7)

87. **One target cluster by default** (`consolidation: single`): all source clusters of a project are sized into
    one target cluster. Each source cluster's demand is still computed on its own (mode, utilization, k_arch/k_virt,
    growth, U_max of its class, memory, storage); the target needs the sum. C_req = Σ D_T(c) / U_max(c), so every
    source keeps its headroom; the implied U_max, growth, source SPC and DRR of the target are the demand-weighted
    values of those sums. `per_cluster` restores one target cluster per source cluster. A project with one source
    cluster gives exactly the same result in both modes (golden cases unchanged).
88. **Growth step events once**: per-cluster sizing applied policy events to every cluster; when consolidating they
    are added once to the target total (to the main cluster, not to the database cluster).
89. **Databases keep their own cluster** while `split_clusters: auto`: per-core-licensed DB VMs of every source
    cluster go to one "Consolidated-db" cluster sized for minimum licensed cores; set `split_clusters: false` for a
    strictly single cluster. This is the licensing best practice already in place, not a second consolidation.
90. **One storage architecture and one array**: with `storage_architecture: auto`, the estate is external only if
    every source cluster would be (RISC / all bare metal), otherwise HCI. With external storage, ONE PowerStore is
    sized on the estate's total capacity and IOPS, connected to the hosts of every target cluster, and reported with
    the main cluster; the DB cluster says it uses the shared array. HCI keeps storage in each target cluster's nodes.
91. **Mixed sources** (e.g. x86 + AIX) are merged after their own adjustments; the Monte Carlo and the vCPU/NUMA
    checks of the target use the platform type and workload class of the largest demand. Source licensing lines of
    the merged clusters are added per product.
92. **"SU" renamed "SPECrate-eq"**. "SU" was RefreshSizer's own term. "SPEC Score" was considered and rejected:
    SPEC publishes many benchmarks (CPU int/fp, jbb, power...), so "SPEC score" does not say which scale; and a
    derived figure (per-core result x cores x utilization) labelled "SPEC Score" reads like a published SPEC result,
    which the SPEC fair-use rules ask to avoid - estimates must be identifiable as such. "SPECrate-eq" names the scale
    (SPECrate2017_int_base) and says "equivalent". Formula symbols: SU_cap -> Cap, SU_host -> Cap_host. Field names
    in project.json (su_cap_total, su_host...) are unchanged for compatibility; runs saved before this change keep
    "SU" in their stored formula texts. The label is one constant (`refreshsizer/units.py`) plus `unit` in i18n.yaml.
    A definition line is printed in the presales and full reports and on the Results page.
93. **Label changed to "SPEC Score"** at the user's request (supersedes the label chosen in 92). To keep within
    the SPEC fair-use rules, the presales and full reports (and their Word/PowerPoint versions) and the Results page carry the definition line: "SPEC Score
    (RefreshSizer estimate): CPU throughput on the SPECrate2017_int_base scale ... an estimate derived from published
    results, not a SPEC result", and the SPEC trademark disclaimer is unchanged. French: "Score SPEC".

## PowerStore Gen 3 and Nutanix with PowerStore (change request 8)

94. **PowerStore Gen 3** (1500 / 5500 / 9500, PowerStoreOS 5, released July 2026) added to `catalog/powerstore.yaml`
    and offered by default (`default_generations: [3]`); Gen 2 models stay selectable. Gen 3 data used: 3U two-node
    appliances, E3 NVMe; 40 drive slots on all three models (user-confirmed, see 97; earlier 24 for the 1500) per Dell's InfoHub and shop pages (one Dell spec
    sheet extract lists 24 for all three - to verify); no expansion enclosures yet (announced for a later release),
    so capacity is limited to the base enclosure; TLC 3.84/7.68/15.36 TB and QLC 30.72 TB drive sizes (public review,
    to verify); minimum drives 7 / 11 from Dell's capacity tables. Prices, power and indicative IOPS are placeholders.
95. **Drive media**: `external_storage.media` = tlc (default) | qlc | any. Without it the cheapest-per-TB rule picked
    30.72 TB QLC for general virtualization, which is not a safe default.
96. **Nutanix AHV with external storage** is supported (NCI 7.6, Dell PowerStore Gen 1/2/3 except X): protection
    `external_nutanix` with a 3-node minimum (Nutanix cluster minimum; one source states 3 compute nodes), NVMe/TCP
    forced (any other protocol is replaced, with a note), single PowerStore appliance, software minimums listed in
    the report (PowerStoreOS 5.0.0.2+, AOS 7.6, AHV 11.2). Nutanix on ESXi with external storage is refused (AHV
    only). The CVM reservation is kept on every compute node (conservative: the CVM still runs the Nutanix services;
    its size with external storage may be smaller - adjust nutanix_cvm_vcpu / nutanix_cvm_memory_gb). Licensing: NCI
    per core, no VMware, no extra storage licence. This supersedes decision 83.
97. **Gen 3 data confirmed by the user**: 40 drive slots on the 1500, 5500 and 9500, and drive sizes TLC
    3.84 / 7.68 / 15.36 TB and QLC 30.72 TB. The 1500 is updated from 24 to 40 slots (supersedes that part of 94).

## CPU display (change request 9)

98. **Every CPU is shown as "Name (cores, GHz)"** - cores per CPU and base clock - in the app, the HTML/PDF,
    Word, PowerPoint and Excel reports, the email prompt and the CLI. One helper (`catalog/cpu_info.py`) looks the
    CPU up by catalog id, catalog name or the CPU matcher (exact/pattern or fuzzy >= 0.9 only, to avoid showing
    another CPU's data); a CPU missing from the catalog keeps the cores/GHz from the input data.
99. **Hover details** (cores/threads, base and turbo GHz, L3 cache, TDP, codename): in the app and the HTML report a
    small inline script shows one floating popup (position: fixed, so cards that scroll sideways cannot clip it),
    with a pure-CSS popup as the no-JavaScript fallback; also on keyboard focus. Excel shows the same details as a
    cell note on hover. PDF, Word and PowerPoint cannot show hover popups, so they carry the "(cores, GHz)" text only.

## Fixes after field test (change request 10)

100. **Naive core match was inconsistent with the GHz match** (bug): core match used INSTALLED cores
     (cores x growth / U_max) while GHz match used USED GHz (utilization applied). On a lightly loaded estate this
     gave e.g. 51 hosts (cores) vs 6 (GHz). Both now start from the same measured load as the recommendation:
     used cores = installed cores x (used GHz / installed GHz); they differ from each other only by the clock ratio
     and from the recommendation only by ignoring the per-core performance gain. When the source GHz is unknown the
     installed cores are used (stated in the calculation). Golden A core match: 13 -> 8 (GHz match 6 unchanged);
     inventory-only (assumed 100 % load) is unchanged. The recommended sizing itself never used these values.
101. **CPU hover in Firefox**: the app's static files had no cache headers, so a browser could keep an old app.js;
     they are now served with `Cache-Control: no-cache` and versioned URLs (`?v=<content hash>`). The hover script
     moved to its own file (`static/cpu_tip.js`, plain ES5), and every CPU also carries a native `title` tooltip as
     the fallback in any browser or with scripts blocked (the script removes it on hover, so only one popup shows).
     The CSS-only popup was dropped (it could be clipped by scrolling table cards).
102. **Default target: Nutanix AHV + disaggregated (one PowerStore)** at the user's request: `target_stack:
     nutanix_ahv`, `storage_architecture: external`, listed first and pre-selected in the selector (stack, then
     architecture: Disaggregated / HCI / Auto). Choosing a non-default value is now stored explicitly (VMware used to
     be "no override"; with the new default that would have silently meant Nutanix). The golden reference cases
     are defined on VMware vSphere + vSAN ESA and are now pinned to it in the tests (`tests/baseline.py`), so their
     numbers are unchanged. CLI: `--stack nutanix_esxi` alone implies `--storage hci` (Nutanix on ESXi has no
     external storage). With Nutanix AHV the selector selects NVMe/TCP and greys out the other protocols.
103. **"Disaggregated chosen but HCI sized"**: not reproducible with the current server code (summary and policy
     routes, VMware and Nutanix, single and consolidated estates all size external storage). Two causes are
     addressed: a stale cached app.js whose old rule disabled "Disaggregated" for any Nutanix stack and silently fell
     back to HCI (fixed by no-cache + versioned static files, decision 101), and the empty Results page's "Run
     sizing" button, which uses the saved/default policy - now disaggregated by default.
104. **CPUs without their own SPEC result** (e.g. Xeon E5-2698 v4, 20 cores): the per-core value of the closest
     same-generation SKU with a result (E5-2680 v4, 14 cores: 149 / 28 = 5.32) is scaled by the clock ratio
     (2.2 / 2.4) and multiplied by the CPU's OWN cores - no cores are lost. The benchmark tables now say so: the
     other CPU's row is labelled "basis of the estimate for ..." and a row shows the value actually used.
105. **Results tested before the SPEC CPU2017 release (June 2017)** used early compilers and score low. When they
     are the only exact results for a CPU and a same-generation estimate is higher, the estimate is used (a too-low
     source SPC shrinks the measured demand -> undersizing); the published result stays listed with its URL and
     sets the lower bound of the uncertainty band (Monte Carlo). Example: E5-2698 v4, Inspur NF5280M4, 143 base,
     tested Dec 2016 -> 3.58 per core listed, 4.88 used, band 3.58-5.37, confidence MEDIUM.
106. **Curated result URLs**: spec.org is not reachable from the build environment, so the curated seeds had no
     links ("URL to be refreshed"). URLs found by web search were added for E5-2680 v3/v4 (HPE DL380 Gen9, 2023)
     and the new E5-2698 v4 row; the E5-2680 v3 test date was corrected (2023-05, not 2017-10). The other curated
     rows get their links from "Update from spec.org" (Benchmarks page / `refreshsizer bench update`) on a machine
     with internet access.
107. **Email prompt: "Why this benchmark"** (EN/FR) is part of the facts - independent SPEC consortium with reviewed,
     fully documented results; throughput metric matching consolidated/virtualized servers; integer suite for
     business workloads; base (not vendor-tuned peak) for cross-vendor comparability; coverage of old and new CPUs;
     why not cores/GHz, consumer tests or application-specific benchmarks. A rule asks the AI for one or two plain
     sentences (a short paragraph for a technical audience) built only from those facts.
108. **Reports list**: newest first (modification time, then name), checkboxes with select-all, "Download selected"
     (one .zip built in memory, named `<project>_reports_<date>.zip`) and "Delete selected" (browser confirmation;
     only files of the project's own reports folder can be selected - other names give 404). The `project.json` in
     that list is an export copy; deleting it does not affect the project.
109. **Powered-off VMs and templates**: still excluded from CPU, memory, vCPU ratios, VM counts and licensing
     (`exclude_powered_off`, `exclude_templates`), but no longer dropped at import - every parser (RVTools, Live
     Optics, universal) keeps them in `Cluster.inactive_workloads`. Their disks occupy capacity and migrate, so the
     storage sizing adds their used capacity (provisioned in inventory-only mode) by default
     (`storage_include_powered_off: true`); turn it off when the customer confirms they will be deleted. When the
     storage basis falls back to datastore/array totals they are not added (those totals already include them).
     With a database split, only the non-database target part carries them. The Ingest summary lists them per
     cluster (count, vCPU, RAM, storage, VM list), a customer question asks whether they will be migrated or
     deleted, the storage design shows "of which powered-off VMs / templates", and the email prompt mentions it.
     Live Optics imports now also state how many VMs were excluded (only RVTools did). Example (real Live Optics
     VMware file): 48 of 94 VMs powered off / templates, 6.1 TiB of 15.3 TiB used storage that was missing.
110. **P2V ratio for physical servers without utilization data** (inventory only): CPU demand of each such server
     = its capacity (SPEC Score of its CPUs x cores) / its pCore:vCore ratio R (3:1 = three of today's physical
     cores per performance-equivalent new core). R per server comes from, most specific first: the manual template
     column `p2v_ratio`, a `host:<name>` override, a `host:<glob>` pattern (exact > pattern, longer pattern first),
     a `cluster:<name or glob>` override, then the global `p2v_ratio` (default 1 = full capacity = previous behaviour).
     "Physical" = hypervisor bare-metal, or a cluster without any VM list. Virtualized hosts without utilization keep
     `inventory_assumed_util`. Measured utilization always replaces the ratio. P2V servers also get the 7 %
     bare-metal -> virtual overhead (k_virt). The 100/70/50 % sensitivity rows are now relative to this inventory
     demand. Ratios are set on the Ingest summary (global, per cluster, per server, patterns) and saved in
     policy.yaml (`p2v_ratio`, `p2v_overrides`); the Policy page keeps them when saved. The vCPU:pCore check does
     not apply to these servers (they have no VM list).
111. **Naive core match on mixed estates** (bug, field report: 40 hosts): used cores were installed cores x (used GHz
     / installed GHz). With sources lacking a clock speed (unknown CPU) or hosts (new VMs), used GHz included them
     but installed GHz did not, giving more "used" cores than installed (819 of 546). Used cores are now each source
     cluster's measured demand expressed in its own cores (D0 / source SPEC per core), summed when consolidating
     (`used_cores_equiv`). Same estate: 27 hosts.
112. **physc_p95 on x86**: it means MEASURED physical cores busy at p95. The demand step is now titled "Workload
     demand" for x86 (was "POWER LPAR demand"), an unknown x86 clock is 2.5 GHz for the GHz comparison (3.5 stays
     for POWER), and a CHECK flag is raised when a host's workloads declare more busy cores than the host has -
     the usual sign that core or vCPU counts were typed in. The manual template README and header comments say
     to leave it empty when usage is unknown, so the P2V ratio applies.
