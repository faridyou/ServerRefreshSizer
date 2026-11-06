"""RefreshSizer local web UI (primary interface): FastAPI + Jinja2 + HTMX (vendored, no CDN, no build step).

All computations go through refreshsizer.workflow / existing modules - the same functions as the CLI."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from refreshsizer import __version__
from refreshsizer.report.build import translator
from refreshsizer.report.explain import fmt, money, pct
from refreshsizer.ui import services
from refreshsizer.ui.jobs import JobManager
from refreshsizer.ui.policy_form import form_groups, parse_form
from refreshsizer.ui.projects import ProjectStore

UI_DIR = Path(__file__).resolve().parent
LANGS = ("en", "fr")


def _linkify(s: Any) -> str:
    from markupsafe import Markup, escape

    txt = str(escape("" if s is None else s))
    return Markup(re.sub(r"(https?://[^\s<]+)", r'<a href="\1" target="_blank" rel="noopener">\1</a>', txt))


def create_app(store: ProjectStore | None = None, jobs: JobManager | None = None) -> FastAPI:
    app = FastAPI(title="RefreshSizer", version=__version__, docs_url=None, redoc_url=None)
    app.state.store = store or ProjectStore()
    app.state.jobs = jobs or JobManager()
    app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")
    templates = Jinja2Templates(directory=str(UI_DIR / "templates"))
    env = templates.env
    env.filters.update({"fmt": fmt, "money": money, "pct": pct, "linkify": _linkify})

    def S() -> ProjectStore:
        return app.state.store  # type: ignore[no-any-return]

    def J() -> JobManager:
        return app.state.jobs  # type: ignore[no-any-return]

    def lang_of(request: Request) -> str:
        lang = request.cookies.get("rs_lang", "en")
        return lang if lang in LANGS else "en"

    def render(request: Request, name: str, ctx: dict[str, Any] | None = None, status: int = 200) -> HTMLResponse:
        lang = lang_of(request)
        base = {"request": request, "t": translator(lang), "lang": lang, "version": __version__}
        base.update(ctx or {})
        return templates.TemplateResponse(request, name, base, status_code=status)

    def proj(name: str) -> tuple[str, dict[str, Any]]:
        try:
            S().path(name)
        except KeyError as e:
            raise HTTPException(404, "project not found") from e
        return name, S().meta(name)

    def effective_policy(n: str, extra: dict[str, Any] | None = None) -> Any:
        """Policy as the sizing will see it (defaults < Policy sheet < policy.yaml < extra); None if invalid."""
        from refreshsizer import workflow

        project = S().project(n)
        try:
            return workflow.make_policy(project.policy_overrides if project else None, S().policy_overrides(n),
                                        extra)
        except ValueError:
            return None

    def target_ctx(n: str) -> dict[str, Any]:
        from refreshsizer.ui import target

        return target.context(effective_policy(n))

    def go(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    def start(kind: str, name: str | None, fn: Any) -> RedirectResponse:
        job = J().submit(kind, name, fn)
        return go(f"/jobs/{job.id}")

    # ---------------------------------------------------------------- language, jobs
    @app.post("/lang/{lang}")
    def set_lang(lang: str, request: Request) -> RedirectResponse:
        r = go(request.headers.get("referer") or "/")
        r.set_cookie("rs_lang", lang if lang in LANGS else "en", max_age=3600 * 24 * 365, samesite="lax")
        return r

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(job_id: str, request: Request) -> Response:
        job = J().get(job_id)
        if job is None:
            raise HTTPException(404)
        partial = request.headers.get("HX-Request") == "true"
        if partial and job.status == "done" and job.redirect:
            return Response(status_code=204, headers={"HX-Redirect": job.redirect})
        return render(request, "_job.html" if partial else "job.html", {"job": job})

    # ---------------------------------------------------------------- home / projects
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return render(request, "home.html", {"projects": S().list_projects()})

    @app.post("/projects")
    def new_project(display: str = Form(...)) -> RedirectResponse:
        name = S().create(display or f"project {datetime.now():%Y-%m-%d %H%M}")
        return go(f"/p/{name}/upload")

    @app.post("/p/{name}/duplicate")
    def duplicate(name: str) -> RedirectResponse:
        proj(name)
        return go(f"/p/{S().duplicate(name)}/upload")

    @app.post("/p/{name}/delete")
    def delete(name: str) -> RedirectResponse:
        proj(name)
        S().delete(name)
        return go("/")

    @app.get("/p/{name}")
    def open_project(name: str) -> RedirectResponse:
        proj(name)
        stage = S().meta(name).get("stage")
        return go(f"/p/{name}/" + {"sized": "results", "ingested": "summary"}.get(stage or "", "upload"))

    # ---------------------------------------------------------------- 1 upload
    @app.get("/p/{name}/upload", response_class=HTMLResponse)
    def upload_page(name: str, request: Request, error: str = "") -> HTMLResponse:
        n, meta = proj(name)
        return render(request, "upload.html", {"name": n, "meta": meta, "tab": "upload", "error": error,
                                               "files": services.file_formats(S(), n)})

    @app.post("/p/{name}/files")
    async def upload_files(name: str, request: Request, files: list[UploadFile] = File(...)) -> Response:
        n, _ = proj(name)
        errors = []
        for f in files:
            try:
                S().add_file(n, f.filename or "upload", await f.read())
            except ValueError as e:
                errors.append(str(e))
        if request.headers.get("HX-Request") == "true":
            return render(request, "_files.html", {"name": n, "files": services.file_formats(S(), n),
                                                   "error": "; ".join(errors)})
        return go(f"/p/{n}/upload" + (f"?error={'; '.join(errors)}" if errors else ""))

    @app.post("/p/{name}/files/remove")
    def remove_file(name: str, filename: str = Form(...)) -> RedirectResponse:
        n, _ = proj(name)
        S().remove_file(n, filename)
        return go(f"/p/{n}/upload")

    @app.get("/template")
    def template(lang: str = "en") -> FileResponse:
        from refreshsizer.ingest.manual_template import generate_template
        from refreshsizer.paths import data_dir

        lg = lang if lang in LANGS else "en"
        p = generate_template(data_dir() / "templates" / f"refreshsizer_template_{lg}.xlsx", lg)
        return FileResponse(p, filename=p.name)

    @app.post("/p/{name}/ingest")
    def ingest(name: str) -> RedirectResponse:
        n, _ = proj(name)
        return start("ingest", n, lambda progress, job: services.run_ingest(S(), n, progress))

    # ---------------------------------------------------------------- 2 mapping review
    @app.get("/p/{name}/mapping", response_class=HTMLResponse)
    def mapping_page(name: str, request: Request, saved: int = -1) -> HTMLResponse:
        from refreshsizer.ingest.universal.mapping_rules import ALL_FIELDS

        n, meta = proj(name)
        project = S().project(n)
        entries = S().review(n)
        sheets = project.ingest.sheets if project else []
        return render(request, "mapping.html", {
            "name": n, "meta": meta, "tab": "mapping", "entries": entries, "fields": ALL_FIELDS,
            "units": ["", "GiB", "MiB", "TiB", "KiB", "GHz", "MHz", "pct"], "saved": saved,
            "ignored": [s for s in sheets if not s.used], "project": project})

    @app.post("/p/{name}/mapping")
    async def mapping_save(name: str, request: Request) -> Response:
        n, _ = proj(name)
        form = await request.form()
        action = str(form.get("action", "apply"))
        entries = S().review(n)
        decisions: dict[tuple[str, str, str], tuple[str | None, str | None]] = {}
        if action == "accept_threshold":
            thr = float(str(form.get("threshold", "0.6")).replace(",", "."))
            for e in entries:
                tgt = (e.proposed or e.target) if e.confidence >= thr else None
                decisions[(e.file, e.sheet, e.column)] = (tgt, e.unit)
        else:
            for i, e in enumerate(entries):
                tgt = str(form.get(f"t{i}", e.target or ""))
                unit = str(form.get(f"u{i}", e.unit or ""))
                if form.get(f"x{i}"):
                    tgt = ""
                decisions[(e.file, e.sheet, e.column)] = (tgt or None, unit or None)
        services.apply_mapping(S(), n, decisions)
        if action == "save_profile":
            services.save_profiles_globally(S(), n)
        return start("ingest", n, lambda progress, job: services.run_ingest(S(), n, progress))

    # ---------------------------------------------------------------- 3 ingest summary
    @app.get("/p/{name}/summary", response_class=HTMLResponse)
    def summary_page(name: str, request: Request, error: str = "") -> HTMLResponse:
        from refreshsizer.ingest.universal.summary import estate_summary

        n, meta = proj(name)
        project = S().project(n)
        rows = estate_summary(project) if project else []
        keys = ["hosts", "sockets", "cores", "ram_gb", "vms", "vcpu", "vram_gb", "storage_prov_tib",
                "storage_used_tib", "iops"]
        total = {k: round(sum(r[k] for r in rows), 2) for k in keys}
        return render(request, "summary.html", {"name": n, "meta": meta, "tab": "summary", "project": project,
                                                "rows": rows, "total": total, "error": error, "tgt": target_ctx(n),
                                                "open_target": True})

    # ---------------------------------------------------------------- 4 policy
    @app.get("/p/{name}/policy", response_class=HTMLResponse)
    def policy_page(name: str, request: Request, error: str = "", saved: int = 0) -> HTMLResponse:
        n, meta = proj(name)
        t = translator(lang_of(request))
        return render(request, "policy.html", {"name": n, "meta": meta, "tab": "policy", "error": error,
                                               "saved": saved, "groups": form_groups(S().policy_overrides(n), t),
                                               "yaml": _policy_yaml(S(), n), "tgt": target_ctx(n),
                                               "open_target": False})

    @app.post("/p/{name}/policy")
    async def policy_save(name: str, request: Request) -> Response:
        n, _ = proj(name)
        raw = await request.form()
        form = {k: str(v) for k, v in raw.items()}
        from refreshsizer.ui import target

        try:
            ov = parse_form(form)
            saved = S().policy_overrides(n)
            if "vendor" in raw:  # the form carries the target selector
                ov = target.merge_into_saved(ov, target.overrides_from_form(raw))
            else:  # keep the saved target selection
                ov.update({k: saved[k] for k in ("target_platforms", "target_cpu_vendors", "target_cpus")
                           if k in saved})
            from refreshsizer import workflow

            workflow.make_policy(None, ov)
        except Exception as e:  # validation error -> show it on the form
            from refreshsizer.ui.jobs import friendly_error

            return go(f"/p/{n}/policy?error={quote(friendly_error(e))[:600]}")
        S().save_policy(n, ov)
        if form.get("then") == "size":
            return start("sizing", n, lambda progress, job: services.run_size(S(), n, {}, "policy", progress))
        return go(f"/p/{n}/policy?saved=1")

    @app.post("/p/{name}/policy/reset")
    def policy_reset(name: str) -> RedirectResponse:
        n, _ = proj(name)
        S().reset_policy(n)
        return go(f"/p/{n}/policy?saved=1")

    # ---------------------------------------------------------------- sizing / 5 results / 6 what-if
    @app.post("/p/{name}/size")
    async def size(name: str, request: Request) -> RedirectResponse:  # also validates the policy
        n, _ = proj(name)
        raw = await request.form()
        form = {k: str(v) for k, v in raw.items()}
        from refreshsizer import workflow
        from refreshsizer.ui import target

        def num(k: str) -> float | None:
            v = form.get(k, "").strip().replace(",", ".")
            return float(v) if v else None

        try:
            years = num("years")
            ov = workflow.overrides_from_flags(growth=num("growth"), years=int(years) if years else None,
                                               objective=form.get("objective") or None, u_max=num("u_max"))
            if "vendor" in raw or "plat" in raw:  # target selector / "use this scenario"
                tgt = target.overrides_from_form(raw) if "vendor" in raw else {
                    "target_platforms": raw.getlist("plat") or None, "target_cpu_vendors": None,
                    "target_cpus": raw.getlist("cpu") or None}
                if form.get("save_target"):  # summary page: remember the customer's choice in policy.yaml
                    saved = target.merge_into_saved(S().policy_overrides(n), tgt)
                    workflow.make_policy(None, saved)
                    S().save_policy(n, saved)
                else:
                    ov.update(tgt)
            project = S().project(n)
            # validate the full policy now (defaults < Policy sheet < policy.yaml < what-if) instead of in the job
            workflow.make_policy(project.policy_overrides if project else None, S().policy_overrides(n), ov)
        except ValueError as e:
            from refreshsizer.ui.jobs import friendly_error

            back = {"whatif": "whatif", "results": "results"}.get(form.get("from", ""), "summary")
            return go(f"/p/{n}/{back}?error={quote(friendly_error(e))[:600]}")
        label = form.get("label", "").strip() or ("what-if" if ov else "baseline")
        if form.get("from") == "summary" and not form.get("label"):
            label = "baseline"
        return start("sizing", n, lambda progress, job: services.run_size(S(), n, ov, label, progress))

    @app.get("/p/{name}/results", response_class=HTMLResponse)
    def results_page(name: str, request: Request, run: str = "", error: str = "") -> HTMLResponse:
        n, meta = proj(name)
        project, rec = services.project_for_run(S(), n, run or None)
        ctx: dict[str, Any] = {"name": n, "meta": meta, "tab": "results", "project": project, "run": rec,
                               "runs": S().runs(n), "error": error}
        if project is not None and project.sizing:
            from refreshsizer.report.build import build_context

            ctx["r"] = build_context(project, lang_of(request))
        return render(request, "results.html", ctx)

    @app.get("/p/{name}/whatif", response_class=HTMLResponse)
    def whatif_page(name: str, request: Request, error: str = "") -> HTMLResponse:
        n, meta = proj(name)
        project = S().project(n)
        pol = None
        if project is not None:
            from refreshsizer import workflow
            from refreshsizer.ui.jobs import friendly_error

            try:
                pol = workflow.make_policy(project.policy_overrides, S().policy_overrides(n))
            except ValueError as e:
                error = error or friendly_error(e)
                pol = workflow.make_policy()
        return render(request, "whatif.html", {"name": n, "meta": meta, "tab": "whatif", "project": project,
                                               "runs": S().runs(n), "policy": pol, "error": error,
                                               "tgt": target_ctx(n), "open_target": False})

    @app.get("/p/{name}/compare", response_class=HTMLResponse)
    def compare_page(name: str, request: Request, a: str = "", b: str = "") -> HTMLResponse:
        from refreshsizer.engine.results import SizingRun

        n, meta = proj(name)
        cols: list[dict[str, Any]] = []
        for rid in (a, b):
            r = S().run(n, rid) if rid else None
            if r:
                cols.append({"run": r, "sizing": SizingRun.model_validate(r["sizing"])})
        clusters = sorted({c.demand.cluster for col in cols for c in col["sizing"].clusters})
        return render(request, "compare.html", {"name": n, "meta": meta, "tab": "whatif", "cols": cols,
                                                "clusters": clusters})

    # ---------------------------------------------------------------- 7 reports
    @app.get("/p/{name}/reports", response_class=HTMLResponse)
    def reports_page(name: str, request: Request) -> HTMLResponse:
        n, meta = proj(name)
        d = S().reports_dir(n)
        files = sorted((p for p in d.iterdir() if p.is_file()), key=lambda p: p.name)
        return render(request, "reports.html", {"name": n, "meta": meta, "tab": "reports", "runs": S().runs(n),
                                                "files": [{"name": p.name, "kb": f"{p.stat().st_size / 1024:,.0f}",
                                                           "time": datetime.fromtimestamp(p.stat().st_mtime)
                                                           .strftime("%Y-%m-%d %H:%M")} for p in files],
                                                "sized": bool(S().runs(n))})

    @app.post("/p/{name}/reports")
    async def reports_make(name: str, request: Request) -> RedirectResponse:
        n, _ = proj(name)
        form = await request.form()
        formats = [f for f in ("html", "xlsx", "pdf") if form.get(f)] or ["html", "xlsx", "pdf"]
        lang = str(form.get("lang", "en"))
        rid = str(form.get("run", "")) or None
        return start("reports", n, lambda progress, job: services.run_reports(S(), n, rid, formats,
                                                                              lang if lang in LANGS else "en",
                                                                              progress))

    def _report_file(n: str, filename: str) -> Path:
        d = S().reports_dir(n).resolve()
        p = (d / Path(filename).name).resolve()
        if p.parent != d or not p.is_file():
            raise HTTPException(404)
        return p

    @app.get("/p/{name}/reports/{filename}")
    def report_download(name: str, filename: str) -> FileResponse:
        n, _ = proj(name)
        p = _report_file(n, filename)
        return FileResponse(p, filename=p.name)

    @app.get("/p/{name}/reports/{filename}/view")
    def report_view(name: str, filename: str) -> FileResponse:
        n, _ = proj(name)
        p = _report_file(n, filename)
        if p.suffix.lower() != ".html":
            raise HTTPException(404)
        return FileResponse(p, media_type="text/html")

    # ---------------------------------------------------------------- 10 benchmarks
    @app.get("/benchmarks", response_class=HTMLResponse)
    def bench_page(request: Request, q: str = "", job: str = "", added: str = "") -> HTMLResponse:
        from refreshsizer.benchmarks.cpu_matcher import match_cpu
        from refreshsizer.benchmarks.curated_db import ensure_db
        from refreshsizer.benchmarks.spc import resolve_spc

        cache = ensure_db()
        match = match_cpu(q) if q else None
        if q:
            rows = cache.query(match.cpu_id) if match else cache.search(q)
            if not rows and match is None:
                rows = cache.search(q)
        else:
            rows = cache.query(suite="cpu2017")
        resolved = [resolve_spc(match.cpu_id, chips=c, cache=cache) for c in (1, 2)] if match else []
        jb = J().get(job) if job else None
        return render(request, "benchmarks.html", {
            "q": q, "rows": sorted(rows, key=lambda r: (r.cpu_name or "", r.system_name or "")), "match": match,
            "resolved": resolved, "job": jb, "added": added,
            "counts": {s: cache.count(s) for s in ("curated", "live", "user")},
            "last_update": cache.get_meta("last_update_cpu2017")})

    @app.post("/benchmarks/update")
    def bench_update() -> RedirectResponse:
        return start("bench_update", None, services.run_bench_update)

    @app.post("/benchmarks/user")
    def bench_user(cpu: str = Form(...), value_base: float = Form(...), enabled_cores: int = Form(...),
                   chips: int = Form(2), system: str = Form(""), url: str = Form(""),
                   note: str = Form("")) -> RedirectResponse:
        from refreshsizer.benchmarks.cpu_matcher import match_cpu
        from refreshsizer.benchmarks.curated_db import ensure_db
        from refreshsizer.catalog.loader import slugify
        from refreshsizer.model.entities import BenchmarkResult

        if value_base <= 0 or enabled_cores <= 0:
            raise HTTPException(400, "value and cores must be positive")
        m = match_cpu(cpu)
        cid = m.cpu_id if m else slugify(cpu)
        r = BenchmarkResult(id=f"user-{cid}-{datetime.now():%Y%m%d%H%M%S}", suite="cpu2017",
                            metric="SPECrate2017_int_base", value_base=value_base, system_name=system or None,
                            cpu_name=m.name if m else cpu, cpu_model_id=cid, chips=chips,
                            enabled_cores=enabled_cores, result_url=url or "user-provided",
                            retrieved_at=datetime.now().isoformat(timespec="seconds"), source="user",
                            notes=note or None, sponsor="user")
        ensure_db().upsert([r])
        return go(f"/benchmarks?q={cpu}&added={r.id}")

    return app


def _policy_yaml(store: ProjectStore, name: str) -> str:
    f = store.path(name) / "policy.yaml"
    return f.read_text(encoding="utf-8") if f.exists() else ""
