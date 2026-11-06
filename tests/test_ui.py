"""Web UI tests (FastAPI TestClient): every fixture end to end, golden A/B/C equal to the CLI path, 127.0.0.1 only."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from refreshsizer import workflow
from refreshsizer.engine.results import SizingRun
from refreshsizer.ui.app import create_app
from refreshsizer.ui.jobs import JobManager
from refreshsizer.ui.projects import MAX_RUNS, ProjectStore

TOL = 0.005
FIXTURES = ["liveoptics_estate.xlsx", "rvtools_estate.xlsx", "manual_template_en.xlsx", "manual_template_fr.xlsx",
            "aix_nmon_summary.csv", "oracle_awr_summary.csv", "messy_customer_fr.xlsx", "messy_semicolon.csv",
            "model_only_inventory.xlsx"]


class UI:
    def __init__(self, root: Path):
        self.store = ProjectStore(root / "projects")
        self.jobs = JobManager()
        self.app = create_app(self.store, self.jobs)
        self.c = TestClient(self.app)

    def new(self, display: str) -> str:
        r = self.c.post("/projects", data={"display": display})
        assert r.status_code == 200
        return r.url.path.split("/")[2]

    def upload(self, name: str, *paths: Path) -> str:
        files = [("files", (p.name, p.read_bytes())) for p in paths]
        r = self.c.post(f"/p/{name}/files", files=files)
        assert r.status_code == 200
        return r.text

    def job(self, r: Any) -> Any:
        assert r.status_code == 200, r.text[:500]
        jid = r.url.path.rsplit("/", 1)[-1]
        j = self.jobs.wait(jid)
        assert j.status == "done", (j.error, j.log)
        page = self.c.get(f"/jobs/{jid}", headers={"HX-Request": "true"})
        assert page.status_code == 204 and page.headers["HX-Redirect"] == j.redirect
        return j

    def ingest(self, name: str) -> Any:
        return self.job(self.c.post(f"/p/{name}/ingest"))

    def size(self, name: str, **form: str) -> Any:
        return self.job(self.c.post(f"/p/{name}/size", data=form))

    def sizing(self, name: str) -> SizingRun:
        p = self.store.project(name)
        assert p and p.sizing
        return SizingRun.model_validate(p.sizing)


@pytest.fixture()
def ui(tmp_path: Path) -> UI:
    return UI(tmp_path)


def approx(v: float, ref: float) -> bool:
    return abs(v - ref) <= abs(ref) * TOL


def same_clusters(ui_run: SizingRun, cli_run: SizingRun) -> None:
    a = [c.model_dump(mode="json") for c in ui_run.clusters]
    b = [c.model_dump(mode="json") for c in cli_run.clusters]
    assert a == b  # identical numbers, candidates, Monte Carlo (fixed seed), explanations


# ------------------------------------------------------------------------------------- end to end
@pytest.mark.parametrize("fixture", FIXTURES)
def test_every_fixture_end_to_end(ui: UI, fixtures_dir: Path, fixture: str) -> None:
    name = ui.new(fixture)
    paths = [fixtures_dir / fixture]
    if fixture == "liveoptics_estate.xlsx":
        paths.append(fixtures_dir / "liveoptics_timeseries.csv")
    html = ui.upload(name, *paths)
    expect = {"liveoptics_estate.xlsx": "Live Optics", "rvtools_estate.xlsx": "RVTools",
              "aix_nmon_summary.csv": "AIX nmon", "oracle_awr_summary.csv": "Oracle AWR",
              "messy_customer_fr.xlsx": "universal"}
    assert expect.get(fixture, "") in html
    j = ui.ingest(name)
    if j.redirect.endswith("/mapping"):
        page = ui.c.get(j.redirect)
        assert page.status_code == 200 and "conf." in page.text
        ui.job(ui.c.post(f"/p/{name}/mapping", data={"action": "accept_threshold", "threshold": "0.6"}))
    s = ui.c.get(f"/p/{name}/summary")
    assert s.status_code == 200 and "q_en" in s.text and "q_fr" in s.text
    j = ui.size(name)
    res = ui.c.get(j.redirect)
    assert res.status_code == 200 and "Show calculation" in res.text and "data:image/png;base64," in res.text
    rid = ui.store.runs(name)[0]["id"]
    ui.job(ui.c.post(f"/p/{name}/reports", data={"run": rid, "html": "1", "xlsx": "1", "pdf": "1", "lang": "en"}))
    files = {p.name for p in ui.store.reports_dir(name).iterdir()}
    assert {"refreshsizer_report.html", "refreshsizer_report.xlsx", "refreshsizer_report.pdf"} <= files
    for f in ("refreshsizer_report.html", "refreshsizer_report.xlsx", "refreshsizer_report.pdf"):
        assert ui.c.get(f"/p/{name}/reports/{f}").status_code == 200
    view = ui.c.get(f"/p/{name}/reports/refreshsizer_report.html/view")
    assert view.status_code == 200 and view.headers["content-type"].startswith("text/html")


# ------------------------------------------------------------------------------------- golden A/B/C
def test_golden_a_ui_equals_cli(ui: UI, fixtures_dir: Path) -> None:
    f = fixtures_dir / "liveoptics_estate.xlsx"
    name = ui.new("golden A")
    ui.upload(name, f)
    ui.ingest(name)
    ui.size(name)
    d = ui.sizing(name).clusters[0].demand
    assert approx(d.d0, 819.5) and approx(d.d_t, 1648.4) and approx(d.c_req, 2197.8)
    assert approx(d.m_t, 6408) and approx(d.storage_raw_tib, 207.4)
    cli = workflow.size(workflow.ingest([f]), workflow.make_policy())
    same_clusters(ui.sizing(name), cli)


def test_golden_b_ui_equals_cli(ui: UI, fixtures_dir: Path) -> None:
    f = fixtures_dir / "aix_nmon_summary.csv"
    name = ui.new("golden B")
    ui.upload(name, f)
    ui.ingest(name)
    r = ui.c.post(f"/p/{name}/policy", data={"growth.cpu_pct": "10", "then": "save"})
    assert r.status_code == 200
    assert ui.store.policy_overrides(name) == {"growth": {"cpu_pct": 10.0}}
    ui.size(name)
    cs = ui.sizing(name).clusters[0]
    assert approx(cs.demand.d0, 153.0) and approx(cs.demand.c_req, 439.9)
    assert cs.recommended and cs.recommended.label == "R7725 2x AMD EPYC 9175F" and cs.recommended.n_total == 2
    assert cs.recommended.oracle_licenses_active == 16 and cs.recommended.oracle_licenses_all == 32
    cli = workflow.size(workflow.ingest([f]), workflow.make_policy(None, None, {"growth": {"cpu_pct": 10}}))
    same_clusters(ui.sizing(name), cli)


def test_golden_c_ui_equals_cli(ui: UI, fixtures_dir: Path, tmp_path: Path) -> None:
    sheets = pd.read_excel(fixtures_dir / "rvtools_estate.xlsx", sheet_name=None)
    sheets["vHost"] = sheets["vHost"].drop(columns=["CPU usage %"])
    sheets["vCPU"] = sheets["vCPU"].drop(columns=["Overall"])
    f = tmp_path / "rvtools_inventory_only.xlsx"
    with pd.ExcelWriter(f) as xw:
        for k, df in sheets.items():
            df.to_excel(xw, sheet_name=k, index=False)
    name = ui.new("golden C")
    ui.upload(name, f)
    ui.ingest(name)
    ui.size(name)
    cs = ui.sizing(name).clusters[0]
    assert cs.demand.mode.value == "INVENTORY_ONLY"
    assert approx(cs.demand.d0, 1490) and approx(cs.demand.d_t, 2997) and approx(cs.demand.c_req, 3996)
    page = ui.c.get(f"/p/{name}/results")
    assert "100.0%" in page.text and "50.0%" in page.text  # 100/70/50 % table
    cli = workflow.size(workflow.ingest([f]), workflow.make_policy())
    same_clusters(ui.sizing(name), cli)


# ------------------------------------------------------------------------------------- binding
def test_binds_only_to_localhost() -> None:
    from refreshsizer.ui import server

    assert server.HOST == "127.0.0.1"
    port = server.find_free_port(18765)
    srv = server.build_server(port)
    assert srv.config.host == "127.0.0.1"
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    try:
        addrs = {s.getsockname()[0] for sv in srv.servers for s in sv.sockets}
        assert addrs == {"127.0.0.1"}
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
    finally:
        srv.should_exit = True
        th.join(5)


def test_next_free_port() -> None:
    from refreshsizer.ui.server import find_free_port

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        busy = s.getsockname()[1]
        s.listen()
        assert find_free_port(busy) != busy


# ------------------------------------------------------------------------------------- features
def test_projects_list_duplicate_delete(ui: UI, fixtures_dir: Path) -> None:
    name = ui.new("Customer X")
    ui.upload(name, fixtures_dir / "messy_semicolon.csv")
    home = ui.c.get("/")
    assert "Customer X" in home.text
    r = ui.c.post(f"/p/{name}/duplicate")
    assert r.status_code == 200 and len(ui.store.list_projects()) == 2
    copy = [p["name"] for p in ui.store.list_projects() if p["name"] != name][0]
    assert len(ui.store.input_files(copy)) == 1
    ui.c.post(f"/p/{copy}/delete")
    assert [p["name"] for p in ui.store.list_projects()] == [name]
    assert ui.c.get("/p/../etc/upload").status_code == 404
    assert ui.c.get("/p/nope/upload").status_code == 404


def test_upload_rejects_bad_type_and_template_download(ui: UI) -> None:
    name = ui.new("bad")
    r = ui.c.post(f"/p/{name}/files", files=[("files", ("evil.exe", b"MZ"))], headers={"HX-Request": "true"})
    assert "unsupported file type" in r.text
    for lang in ("en", "fr"):
        t = ui.c.get(f"/template?lang={lang}")
        assert t.status_code == 200 and t.content[:2] == b"PK"


def test_mapping_ignore_unit_and_profile(ui: UI, fixtures_dir: Path) -> None:
    from refreshsizer.paths import mapping_profiles_dir

    name = ui.new("mapping")
    ui.upload(name, fixtures_dir / "messy_semicolon.csv")
    assert ui.ingest(name).redirect.endswith("/mapping")
    entries = ui.store.review(name)
    idx = {e.column: i for i, e in enumerate(entries)}
    form = {f"t{i}": (e.target or "") for i, e in enumerate(entries)}
    form.update({f"u{i}": (e.unit or "") for i, e in enumerate(entries)})
    form[f"x{idx['Cluster']}"] = "1"  # ignore the cluster column
    form[f"u{idx['RAM (Go)']}"] = "TiB"  # deliberately change a unit
    form["action"] = "save_profile"
    ui.job(ui.c.post(f"/p/{name}/mapping", data=form))
    p = ui.store.project(name)
    assert p and p.clusters[0].name == "default"  # cluster column ignored
    assert p.clusters[0].hosts[0].ram_gb == 384 * 1024  # unit decision applied
    assert list(mapping_profiles_dir().glob("*.json"))  # saved globally
    m = {e.column: e for e in ui.store.review(name)}
    assert m["Cluster"].target is None and m["RAM (Go)"].unit == "TiB" and m["Sockets"].method == "user"


def test_policy_form_validation_and_reset(ui: UI, fixtures_dir: Path) -> None:
    name = ui.new("policy")
    page = ui.c.get(f"/p/{name}/policy")
    for field in ("growth.cpu_pct", "horizon_years", "u_max.general_virt", "ha_policy", "memory_basis",
                  "protection", "slack", "drr.general_virt", "vcpu_ratio_max.tier0", "vmware_edition",
                  "objective", "inventory_assumed_util"):
        assert f'name="{field}"' in page.text, field
    bad = ui.c.post(f"/p/{name}/policy", data={"horizon_years": "0", "then": "save"})
    assert "horizon" in bad.text and not ui.store.policy_overrides(name)
    ui.c.post(f"/p/{name}/policy", data={"objective": "min_hosts", "slack": "0.25", "then": "save"})
    assert ui.store.policy_overrides(name) == {"objective": "min_hosts", "slack": 0.25}
    ui.c.post(f"/p/{name}/policy/reset")
    assert ui.store.policy_overrides(name) == {}


def test_whatif_runs_kept_and_compare(ui: UI, fixtures_dir: Path) -> None:
    name = ui.new("whatif")
    ui.upload(name, fixtures_dir / "liveoptics_estate.xlsx")
    ui.ingest(name)
    ui.size(name)
    ui.size(name, growth="5", years="3", u_max="0.6", objective="min_hosts", label="low growth")
    runs = ui.store.runs(name)
    assert runs[0]["label"] == "low growth"
    assert runs[0]["overrides"]["growth"] == {"cpu_pct": 5.0} and runs[0]["overrides"]["horizon_years"] == 3
    new = ui.sizing(name).clusters[0].demand
    assert new.years == 3 and new.growth_cpu_pct == 5 and new.u_max == 0.6
    cmp = ui.c.get(f"/p/{name}/compare", params={"a": runs[1]["id"], "b": runs[0]["id"]})
    assert cmp.status_code == 200 and "low growth" in cmp.text and "Prod-Cluster" in cmp.text
    old = ui.c.get(f"/p/{name}/results", params={"run": runs[1]["id"]})
    assert "baseline" in old.text
    for _ in range(MAX_RUNS):
        ui.size(name, growth="10")
    assert len(ui.store.runs(name)) == MAX_RUNS


def test_benchmarks_search_update_user_value(ui: UI) -> None:
    from refreshsizer.benchmarks.spc import resolve_spc

    page = ui.c.get("/benchmarks", params={"q": "EPYC 9355"})
    assert page.status_code == 200 and "964" in page.text and "curated" in page.text
    j = ui.job(ui.c.post("/benchmarks/update"))
    assert "curated" in j.result  # offline fallback message
    assert "curated" in ui.c.get(j.redirect).text
    assert resolve_spc("Intel Xeon Gold 6130", chips=2).method == "generation_avg"
    ui.c.post("/benchmarks/user", data={"cpu": "Intel Xeon Gold 6130", "value_base": "190", "enabled_cores": "32",
                                        "chips": "2", "url": "https://example.org/r1"})
    r = resolve_spc("Intel Xeon Gold 6130", chips=2)
    assert r.method == "user" and abs(r.spc - 190 / 32) < 1e-9


def test_language_toggle(ui: UI) -> None:
    ui.c.post("/lang/fr")
    assert "Nouveau projet" in ui.c.get("/").text
    ui.c.post("/lang/en")
    assert "New project" in ui.c.get("/").text


def test_job_failure_is_reported(ui: UI) -> None:
    name = ui.new("empty")
    r = ui.c.post(f"/p/{name}/ingest")
    j = ui.jobs.wait(r.url.path.rsplit("/", 1)[-1])
    assert j.status == "error" and "no input files" in (j.error or "")
    assert "no input files" in ui.c.get(f"/jobs/{j.id}").text


def test_project_folder_layout(ui: UI, fixtures_dir: Path) -> None:
    name = ui.new("layout")
    ui.upload(name, fixtures_dir / "messy_semicolon.csv")
    ui.ingest(name)
    ui.c.post(f"/p/{name}/policy", data={"growth.cpu_pct": "12", "then": "save"})
    ui.size(name)
    d = ui.store.path(name)
    for item in ("input/messy_semicolon.csv", "project.json", "policy.yaml", "mapping_review.json", "meta.json"):
        assert (d / item).exists(), item
    assert list((d / "mappings").glob("*.json")) and list((d / "runs").glob("*.json"))
    assert json.loads((d / "meta.json").read_text())["stage"] == "sized"


def test_invalid_whatif_is_rejected_not_hung(ui: UI, fixtures_dir: Path) -> None:
    """Regression: U_max <= 0 made the HA loop spin for ever (job stuck at 2 %)."""
    name = ui.new("hang")
    ui.upload(name, fixtures_dir / "liveoptics_estate.xlsx")
    ui.ingest(name)
    before = len(ui.jobs.jobs)
    for bad in ({"u_max": "-0.5"}, {"u_max": "0"}, {"u_max": "75"}, {"growth": "-100"}, {"years": "0.5"}):
        r = ui.c.post(f"/p/{name}/size", data={"from": "whatif", **bad})
        assert r.status_code == 200 and "/whatif" in str(r.url), bad
        assert "class=\"err\"" in r.text, bad
    assert len(ui.jobs.jobs) == before  # no job was started


def test_invalid_policy_file_reports_error(ui: UI, fixtures_dir: Path) -> None:
    name = ui.new("badpolicy")
    ui.upload(name, fixtures_dir / "liveoptics_estate.xlsx")
    ui.ingest(name)
    (ui.store.path(name) / "policy.yaml").write_text("u_max: {default: -1}\n", encoding="utf-8")
    r = ui.c.post(f"/p/{name}/size")
    assert "/summary" in str(r.url) and "u_max.default" in r.text


def test_sizing_progress_moves_during_optimizer(ui: UI, fixtures_dir: Path) -> None:
    name = ui.new("progress")
    ui.upload(name, fixtures_dir / "liveoptics_estate.xlsx")
    ui.ingest(name)
    j = ui.size(name)
    steps = [m for m in j.log if "optimizer" in m]
    assert len(steps) >= 10 and j.elapsed.endswith("s")


def test_stuck_job_does_not_block_other_projects(ui: UI) -> None:
    import threading as th

    gate = th.Event()
    a = ui.jobs.submit("sizing", "proj-a", lambda progress, job: (gate.wait(10), None)[1])
    b = ui.jobs.submit("sizing", "proj-b", lambda progress, job: "/done")
    assert ui.jobs.wait(b.id, 5).status == "done"  # not queued behind the stuck job
    assert ui.jobs.get(a.id).status == "running"  # type: ignore[union-attr]
    c = ui.jobs.submit("reports", "proj-a", lambda progress, job: "/after")
    time.sleep(0.2)
    assert c.status == "queued"  # same project: waits for its own previous job
    gate.set()
    assert ui.jobs.wait(c.id, 5).status == "done"


def _target_form(plats: list[str], vendor: str = "both", extra: dict[str, str] | None = None,
                 cpus: list[str] | None = None) -> dict[str, Any]:
    """httpx form data: repeated fields as lists."""
    return {"vendor": vendor, "plat": plats, "cpu": cpus or [], **(extra or {})}


INTEL = ["R670", "R770", "R770AP", "R470", "R570"]
AMD = ["R6715", "R7715", "R6725", "R7725"]


def test_target_selection_saved_and_scenarios(ui: UI, fixtures_dir: Path) -> None:
    from refreshsizer.catalog.loader import load_cpus

    name = ui.new("target")
    ui.upload(name, fixtures_dir / "liveoptics_estate.xlsx")
    ui.ingest(name)
    page = ui.c.get(f"/p/{name}/summary")
    assert 'name="plat"' in page.text and "PowerEdge R7725" in page.text
    form = _target_form(INTEL + AMD, "Intel", {"from": "summary", "save_target": "1"})
    j = ui.job(ui.c.post(f"/p/{name}/size", data=form))
    assert ui.store.policy_overrides(name) == {"target_platforms": INTEL, "target_cpu_vendors": ["Intel"]}
    cs = ui.sizing(name).clusters[0]
    assert cs.recommended and load_cpus()[cs.recommended.config.cpu_id].vendor == "Intel"
    res = ui.c.get(j.redirect)
    assert "CPU scenarios" in res.text and "best Intel" in res.text and "Use this scenario" in res.text
    # 'use this scenario' re-runs restricted to that platform + CPU
    alt = cs.scenarios[1]
    form2 = {"from": "results", "plat": alt.config.platform, "cpu": alt.config.cpu_id,
             "label": f"scenario: {alt.label}"}
    ui.job(ui.c.post(f"/p/{name}/size", data=form2))
    new = ui.sizing(name).clusters[0]
    assert new.recommended and new.recommended.label == alt.label
    assert ui.store.runs(name)[0]["label"] == f"scenario: {alt.label}"
    assert ui.store.policy_overrides(name)["target_cpu_vendors"] == ["Intel"]  # saved choice untouched


def test_whatif_target_is_one_off_and_policy_keeps_target(ui: UI, fixtures_dir: Path) -> None:
    from refreshsizer.catalog.loader import load_cpus

    name = ui.new("target2")
    ui.upload(name, fixtures_dir / "liveoptics_estate.xlsx")
    ui.ingest(name)
    ui.job(ui.c.post(f"/p/{name}/size", data=_target_form(AMD, "AMD", {"from": "whatif"})))
    cs = ui.sizing(name).clusters[0]
    assert cs.recommended and load_cpus()[cs.recommended.config.cpu_id].vendor == "AMD"
    assert ui.store.policy_overrides(name) == {}  # what-if does not change the saved policy
    ui.c.post(f"/p/{name}/policy", data=_target_form(["R7725"], "AMD", {"then": "save"}))
    assert ui.store.policy_overrides(name)["target_platforms"] == ["R7725"]
    ui.c.post(f"/p/{name}/policy", data={"slack": "0.25", "then": "save"})  # form without the selector
    assert ui.store.policy_overrides(name)["target_platforms"] == ["R7725"]
    bad = ui.c.post(f"/p/{name}/size", data={"vendor": "both", "from": "whatif"})
    assert "select at least one target platform" in bad.text
