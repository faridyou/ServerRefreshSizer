"""RefreshSizer command line (stage 1 subset; extended in later stages)."""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help="RefreshSizer: performance-normalized server refresh sizing")


@app.command()
def template(out: Path = typer.Option(Path("template.xlsx"), "--out"), lang: str = "en") -> None:
    """Write the manual input template."""
    from refreshsizer.ingest.manual_template import generate_template

    typer.echo(generate_template(out, lang))


def main() -> None:
    app()
