"""RefreshSizer command line (Typer + rich)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from refreshsizer import workflow

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="RefreshSizer: performance-normalized server refresh sizing (Dell PowerEdge 17G).")
bench_app = typer.Typer(no_args_is_help=True, help="Benchmark database (SPEC CPU, rPerf, curated).")
app.add_typer(bench_app, name="bench")
console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")


def _policy(project_overrides: dict[str, Any] | None, policy_file: Path | None, cli: dict[str, Any]):  # type: ignore[no-untyped-def]
    return workflow.make_policy(project_overrides, policy_file, cli)


def _cli_overrides(growth: float | None, years: int | None, objective: str | None, p95_estimate: bool,
                   lang: str | None = None) -> dict[str, Any]:
    return workflow.overrides_from_flags(growth, years, objective, p95_estimate, lang)


def _review_mode(yes: bool, strict: bool) -> str:
    if strict:
        return "strict"
    return "yes" if yes else "interactive"


def _load_project(path: Path):  # type: ignore[no-untyped-def]
    return workflow.load_project(path)


def _save_project(project: Any, out: Path) -> None:
    workflow.save_project(project, out)


@app.command()
def template(out: Path = typer.Option(Path("template.xlsx"), "--out", help="Output .xlsx"),
             lang: str = typer.Option("en", "--lang", help="en | fr")) -> None:
    """Write the manual input template (EN or FR headers)."""
    from refreshsizer.ingest.manual_template import generate_template

    console.print(f"Template written: {generate_template(out, lang)}")


@app.command()
def ingest(files: list[Path] = typer.Argument(..., exists=True, help="Input files (xlsx/xls/xlsm/ods/csv)"),
           out: Path = typer.Option(Path("project.json"), "--out"),
           yes: bool = typer.Option(False, "--yes", help="Auto-accept mappings >= 0.6 confidence"),
           strict: bool = typer.Option(False, "--strict", help="Refuse mappings < 0.9 confidence"),
           no_llm: bool = typer.Option(False, "--no-llm", help="Rules-only mapping"),
           lang: str = typer.Option("en", "--lang"), name: str | None = typer.Option(None, "--name"),
           verbose: bool = typer.Option(False, "-v", "--verbose")) -> None:
    """Ingest inventory/utilization files into a project.json (prints the ingest summary)."""
    _setup_logging(verbose)
    from refreshsizer.ingest.manual_template import write_validation_report
    from refreshsizer.ingest.universal.summary import render_ingest_summary

    project = workflow.ingest(files, review_mode=_review_mode(yes, strict), llm=False if no_llm else "auto",
                              name=name)
    render_ingest_summary(project, console, lang)
    if project.ingest.errors:
        from refreshsizer.ingest.base import ParseResult

        rep = ParseResult(fmt="validation", file=", ".join(project.ingest.files), errors=project.ingest.errors,
                          warnings=project.ingest.warnings)
        vr = write_validation_report(rep, Path(out).with_suffix(".validation.txt"))
        if any(f == "manual_template" for f in project.ingest.formats.values()):
            console.print(f"[red]Blocking validation errors - see {vr}[/red]")
            raise typer.Exit(code=2)
    _save_project(project, out)
    console.print(f"[green]Project written: {out}[/green]")


def _do_size(project: Any, policy: Any) -> Any:
    return workflow.size(project, policy)


def _print_sizing(run: Any) -> None:
    t = Table(title="Sizing results")
    for h in ("cluster", "mode", "confidence", "C_req SU", "recommended", "hosts", "P50/P90", "core-match",
              "GHz-match", "TCO (USD)"):
        t.add_column(h)
    for cs in run.clusters:
        r = cs.recommended
        d = cs.demand
        t.add_row(d.cluster, d.mode.value, d.confidence.value, f"{d.c_req:,.0f}", r.label if r else "none",
                  str(r.n_total) if r else "-",
                  f"{cs.uncertainty.p50_hosts}/{cs.uncertainty.p90_hosts}" if cs.uncertainty else "-",
                  str(cs.naive.core_match_hosts) if cs.naive else "-",
                  str(cs.naive.ghz_match_hosts) if cs.naive else "-", f"{r.tco:,.0f}" if r else "-")
    console.print(t)
    for w in run.warnings:
        console.print(f"[yellow]WARNING:[/yellow] {w}")


@app.command()
def size(project_file: Path = typer.Argument(..., exists=True),
         policy: Path | None = typer.Option(None, "--policy", exists=True, help="policy.yaml overrides"),
         growth: float | None = typer.Option(None, "--growth", help="CPU growth %/yr"),
         years: int | None = typer.Option(None, "--years"),
         objective: str | None = typer.Option(None, "--objective",
                                              help="min_tco|min_hosts|min_licensed_cores|min_power|max_headroom"),
         p95_estimate: bool = typer.Option(False, "--p95-estimate", help="Live Optics: p95 ~= avg+0.8(peak-avg)"),
         out: Path | None = typer.Option(None, "--out", help="Output project (default: overwrite input)"),
         verbose: bool = typer.Option(False, "-v", "--verbose")) -> None:
    """Size a project (adds the 'sizing' section to project.json)."""
    _setup_logging(verbose)
    project = _load_project(project_file)
    pol = _policy(project.policy_overrides, policy, _cli_overrides(growth, years, objective, p95_estimate))
    run = _do_size(project, pol)
    _print_sizing(run)
    _save_project(project, out or project_file)
    console.print(f"[green]Sizing saved: {out or project_file}[/green]")


@app.command()
def report(project_file: Path = typer.Argument(..., exists=True),
           format: str = typer.Option("html,pdf,xlsx", "--format", help="Comma list: html,pdf,xlsx"),
           out: Path = typer.Option(Path("reports"), "--out"), lang: str = typer.Option("en", "--lang"),
           verbose: bool = typer.Option(False, "-v", "--verbose")) -> None:
    """Write the HTML / PDF / Excel report for a sized project."""
    _setup_logging(verbose)
    project = _load_project(project_file)
    if not project.sizing:
        console.print("[red]Project is not sized yet: run `refreshsizer size` first.[/red]")
        raise typer.Exit(code=1)
    for p in workflow.reports(project, out, [f.strip() for f in format.split(",") if f.strip()], lang):
        console.print(f"[green]written[/green] {p}")


@app.command()
def run(files: list[Path] = typer.Argument(..., exists=True), out: Path = typer.Option(Path("reports"), "--out"),
        yes: bool = typer.Option(False, "--yes"), strict: bool = typer.Option(False, "--strict"),
        no_llm: bool = typer.Option(False, "--no-llm"), policy: Path | None = typer.Option(None, "--policy"),
        growth: float | None = typer.Option(None, "--growth"), years: int | None = typer.Option(None, "--years"),
        objective: str | None = typer.Option(None, "--objective"),
        p95_estimate: bool = typer.Option(False, "--p95-estimate"),
        format: str = typer.Option("html,pdf,xlsx", "--format"), lang: str = typer.Option("en", "--lang"),
        name: str | None = typer.Option(None, "--name"), verbose: bool = typer.Option(False, "-v", "--verbose")) -> None:
    """Ingest + size + report in one go."""
    _setup_logging(verbose)
    from refreshsizer.ingest.universal.summary import render_ingest_summary

    project = workflow.ingest(files, review_mode=_review_mode(yes, strict), llm=False if no_llm else "auto",
                              name=name)
    render_ingest_summary(project, console, lang)
    pol = _policy(project.policy_overrides, policy, _cli_overrides(growth, years, objective, p95_estimate, lang))
    sizing = _do_size(project, pol)
    _print_sizing(sizing)
    out.mkdir(parents=True, exist_ok=True)
    _save_project(project, out / "project.json")
    for p in workflow.reports(project, out, [f.strip() for f in format.split(",") if f.strip()], lang):
        console.print(f"[green]written[/green] {p}")


@bench_app.command("update")
def bench_update(suite: str = typer.Option("cpu2017", "--suite", help="cpu2017 | cpu2026"),
                 since: str | None = typer.Option(None, "--since", help="e.g. 2024q1"),
                 offline: bool = typer.Option(False, "--offline"), force: bool = typer.Option(False, "--force"),
                 cpu: str | None = typer.Option(None, "--cpu", help="Only results whose index row mentions this"),
                 max_results: int = typer.Option(200, "--max-results"),
                 verbose: bool = typer.Option(False, "-v", "--verbose")) -> None:
    """Refresh live SPEC results (weekly at most); falls back to the curated offline DB."""
    _setup_logging(verbose)
    from refreshsizer.benchmarks.curated_db import build, ensure_db
    from refreshsizer.benchmarks.spec_fetch import update

    cache = ensure_db()
    build(cache, force=True)
    rep = update(suite, since=since, offline=offline, cpu_filter=cpu, max_results=max_results, force=force,
                 cache=cache)
    console.print(f"suite {suite}: online={rep.online} quarters={rep.quarters_seen} added={rep.results_added}")
    for m in rep.messages:
        console.print(f"  {m}")
    console.print(f"DB {cache.path}: {cache.count()} results ({cache.count('curated')} curated, "
                  f"{cache.count('live')} live)")


@bench_app.command("show")
def bench_show(query: str = typer.Argument(..., help='CPU name, e.g. "EPYC 9355"')) -> None:
    """Show benchmark results and the resolved SPC for a CPU."""
    from refreshsizer.benchmarks.cpu_matcher import match_cpu
    from refreshsizer.benchmarks.curated_db import ensure_db
    from refreshsizer.benchmarks.spc import resolve_spc

    cache = ensure_db()
    m = match_cpu(query)
    console.print(f"match: {m.name if m else 'none'} ({m.cpu_id if m else '-'}, {m.method if m else ''}, "
                  f"confidence {m.confidence if m else 0})")
    rows = cache.query(m.cpu_id) if m else cache.search(query)
    t = Table(title=f"Benchmark results for {query}")
    for h in ("suite", "metric", "base", "cores", "chips", "SPC", "system", "sponsor", "date", "source", "URL"):
        t.add_column(h)
    for r in rows:
        t.add_row(r.suite, r.metric, f"{r.value_base:g}" if r.value_base else "", str(r.enabled_cores or ""),
                  str(r.chips or ""), f"{r.per_core:.2f}" if r.per_core else "", r.system_name or "",
                  r.sponsor or "", r.test_date or "", r.source, r.result_url or "")
    console.print(t)
    if m:
        for chips in (1, 2):
            s = resolve_spc(m.cpu_id, chips=chips, cache=cache)
            console.print(f"resolved SPC ({chips}S): {s.spc:.3f} [{s.method}, {s.confidence.value}] "
                          f"band {s.spc_min:.2f}-{s.spc_max:.2f}", markup=False)
            for f in s.flags:
                console.print(f"   {f}", markup=False)


@app.command()
def ui(port: int = typer.Option(8765, "--port", help="First port to try (next free port if taken)"),
       no_browser: bool = typer.Option(False, "--no-browser", help="Do not open the default browser")) -> None:
    """Start the local web UI (primary interface). Binds to 127.0.0.1 only."""
    from refreshsizer.ui.server import serve

    serve(port=port, open_browser=not no_browser, echo=console.print)


@app.command()
def version() -> None:
    """Print the version."""
    from refreshsizer import __version__

    console.print(json.dumps({"refreshsizer": __version__}))


def main() -> None:
    app()
