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
