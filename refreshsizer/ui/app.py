"""Optional local web UI (FastAPI + HTMX). Binds to localhost only (see `refreshsizer ui`).

Flow: upload files -> ingest (auto-accept >= 0.6) -> editable mapping grid + policy YAML -> size + reports ->
download. Pages are plain HTML forms, so they also work if the HTMX script cannot be loaded (offline)."""

from __future__ import annotations

import html
import re
import uuid
from pathlib import Path

import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from refreshsizer.paths import data_dir

CSS = """body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;background:#f4f3ef;color:#0b0b0b}
header{background:#1f4e79;color:#fff;padding:14px 16px}main{max-width:1100px;margin:0 auto;padding:16px}
.card{background:#fcfcfb;border:1px solid #e4e3df;border-radius:8px;padding:12px 16px;margin:12px 0;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid #e4e3df;padding:4px 6px;
text-align:left}th{background:#eef2f7}button{background:#1f4e79;color:#fff;border:0;border-radius:6px;padding:8px 14px;
cursor:pointer}textarea{width:100%;font-family:monospace}.lo{color:#8f1d1b}.mid{color:#7a5200}.hi{color:#0b5d2a}"""


def page(body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,"
        "initial-scale=1'><title>RefreshSizer</title>"
        "<script src='https://unpkg.com/htmx.org@1.9.12' defer></script>"
        f"<style>{CSS}</style></head><body><header><b>RefreshSizer</b> - local UI (localhost only)</header>"
        f"<main>{body}</main></body></html>")


def _sessions() -> Path:
    d = data_dir() / "ui_sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sid_dir(sid: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", sid):
        raise HTTPException(404)
    d = _sessions() / sid
    if not d.is_dir():
        raise HTTPException(404)
    return d


def _ingest(d: Path):  # type: ignore[no-untyped-def]
    from refreshsizer.ingest.pipeline import ingest_files

    files = sorted((d / "in").iterdir())
    return ingest_files(files, review_mode="yes", llm="auto", name=d.name)


def create_app() -> FastAPI:
    app = FastAPI(title="RefreshSizer UI")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return page("""<div class=card><h2>1. Upload inventory / utilization files</h2>
<form action='/upload' method='post' enctype='multipart/form-data'>
<input type='file' name='files' multiple required> <button type='submit'>Ingest</button></form>
<p>RVTools, Live Optics, manual template, nmon/AWR CSV or any customer xlsx/xls/ods/csv.
Data stays on this machine (only redacted headers/samples go to the LLM, and only if an API key is set).</p></div>""")

    @app.post("/upload", response_class=HTMLResponse)
    async def upload(files: list[UploadFile] = File(...)) -> HTMLResponse:
        sid = uuid.uuid4().hex[:12]
        d = _sessions() / sid / "in"
        d.mkdir(parents=True)
        for f in files:
            name = Path(f.filename or "upload.bin").name
            (d / name).write_bytes(await f.read())
        return _review_page(sid)

    def _review_page(sid: str) -> HTMLResponse:
        from refreshsizer.config.schema import default_policy_dict
        from refreshsizer.ingest.universal.mapping_rules import ALL_FIELDS

        d = _sid_dir(sid)
        proj = _ingest(d)
        (d / "project.json").write_text(proj.model_dump_json(), encoding="utf-8")
        rows = []
        for i, m in enumerate(proj.ingest.mappings):
            if m.method == "parser":
                continue
            cls = "hi" if m.confidence >= 0.9 else "mid" if m.confidence >= 0.6 else "lo"
            opts = "".join(f"<option {'selected' if f == m.target else ''}>{f}</option>" for f in ALL_FIELDS)
            key = html.escape(f"{m.file}|{m.sheet}|{m.column}")
            rows.append(f"<tr><td>{html.escape(m.file)}</td><td>{html.escape(m.sheet)}</td><td>{html.escape(m.column)}"
                        f"</td><td><select name='map::{key}'><option value=''>-</option>{opts}</select></td>"
                        f"<td class={cls}>{m.confidence:.2f}</td><td>{m.method}</td>"
                        f"<td>{html.escape(m.sample or '')}</td><td>{html.escape(m.transformed or '')}</td></tr>")
        est = "".join(f"<li>{html.escape(c.name)}: {len(c.hosts)} hosts, {len(c.workloads)} workloads - "
                      f"{dq.mode.value} ({dq.confidence.value})</li>"
                      for c, dq in zip(proj.clusters, proj.data_quality, strict=False))
        qs = "".join(f"<li>{html.escape(q)}</li>" for q in proj.customer_questions)
        pol = html.escape(yaml.safe_dump({k: default_policy_dict()[k] for k in
                                          ("growth", "horizon_years", "objective", "u_max", "ha_policy")},
                                         sort_keys=False))
        grid = ("<table><tr><th>file</th><th>sheet</th><th>column</th><th>field</th><th>conf.</th><th>method</th>"
                "<th>sample</th><th>transformed</th></tr>" + "".join(rows) + "</table>") if rows else \
            "<p>All files were recognised by dedicated parsers (no universal mapping needed).</p>"
        return page(f"""<div class=card><h2>2. Review</h2><ul>{est}</ul><h3>Questions for the customer</h3><ol>{qs}</ol></div>
<form action='/size/{sid}' method='post' hx-post='/size/{sid}' hx-target='#result' hx-swap='innerHTML'>
<div class=card><h3>Column mappings (editable)</h3>{grid}</div>
<div class=card><h3>Policy overrides (YAML)</h3><textarea name='policy' rows='14'>{pol}</textarea>
<p><label><input type='checkbox' name='fr' value='1'> French report</label></p>
<button type='submit'>3. Size and build reports</button></div></form><div id='result'></div>""")

    @app.post("/size/{sid}", response_class=HTMLResponse)
    async def size(sid: str, request: Request, policy: str = Form(""), fr: str = Form("")) -> HTMLResponse:
        from refreshsizer.config.schema import load_policy
        from refreshsizer.engine.sizing import size_project
        from refreshsizer.ingest.universal.profiles_store import apply_user_edits
        from refreshsizer.report.build import write_reports

        d = _sid_dir(sid)
        form = await request.form()
        edits: dict[tuple[str, str, str], str | None] = {}
        from refreshsizer.model.entities import Project

        saved = Project.model_validate_json((d / "project.json").read_text(encoding="utf-8"))
        old = {(m.file, m.sheet, m.column): m.target for m in saved.ingest.mappings}
        for k, v in form.items():
            if k.startswith("map::"):
                f, s, c = k[5:].split("|", 2)
                val = str(v) or None
                if old.get((f, s, c)) != val:
                    edits[(f, s, c)] = val
        if edits:
            apply_user_edits(sorted((d / "in").iterdir()), edits)
        try:
            ov = yaml.safe_load(policy) or {} if policy.strip() else {}
            if not isinstance(ov, dict):
                raise ValueError("policy must be a mapping")
            proj = _ingest(d)
            pol = load_policy(proj.policy_overrides, ov)
        except (yaml.YAMLError, ValueError) as e:
            return page(f"<div class='card lo'>Policy error: {html.escape(str(e))}</div>")
        run = size_project(proj, pol)
        lang = "fr" if fr else "en"
        out = d / "reports"
        files = write_reports(proj, out, ["html", "pdf", "xlsx"], lang)
        (out / "project.json").write_text(proj.model_dump_json(indent=1), encoding="utf-8")
        rows = "".join(
            f"<tr><td>{html.escape(c.demand.cluster)}</td><td>{c.demand.mode.value}</td>"
            f"<td>{html.escape(c.recommended.label) if c.recommended else 'none'}</td>"
            f"<td>{c.recommended.n_total if c.recommended else '-'}</td>"
            f"<td>{c.uncertainty.p90_hosts if c.uncertainty else '-'}</td>"
            f"<td>{c.naive.core_match_hosts if c.naive else '-'} / {c.naive.ghz_match_hosts if c.naive else '-'}</td></tr>"
            for c in run.clusters)
        links = "".join(f"<li><a href='/download/{sid}/{p.name}'>{p.name}</a></li>"
                        for p in files + [out / "project.json"])
        body = (f"<div class=card><h2>Results</h2><p>{len(edits)} mapping edit(s) saved as mapping profiles.</p>"
                "<table><tr><th>cluster</th><th>mode</th><th>recommended</th><th>hosts</th><th>P90</th>"
                f"<th>core/GHz match</th></tr>{rows}</table><h3>Downloads</h3><ul>{links}</ul></div>")
        if request.headers.get("HX-Request"):
            return HTMLResponse(body)
        return page(body)

    @app.get("/download/{sid}/{name}")
    def download(sid: str, name: str) -> FileResponse:
        d = _sid_dir(sid) / "reports"
        p = (d / Path(name).name).resolve()
        if not p.is_file() or p.parent != d.resolve():
            raise HTTPException(404)
        return FileResponse(p, filename=p.name)

    return app
