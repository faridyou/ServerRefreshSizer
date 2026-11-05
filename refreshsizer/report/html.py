"""HTML report (primary, self-contained single file: inline CSS, base64 PNG charts)."""

from __future__ import annotations

from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from refreshsizer.paths import TEMPLATES_DIR
from refreshsizer.report.explain import fmt, money, pct


def env() -> Environment:
    e = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=select_autoescape(["html", "j2"]),
                    trim_blocks=True, lstrip_blocks=True)
    e.filters["fmt"] = fmt
    e.filters["money"] = money
    e.filters["pct"] = pct
    return e


def render_html(ctx: dict[str, Any], pdf: bool = False) -> str:
    return env().get_template("report.html.j2").render(**ctx, pdf=pdf)
