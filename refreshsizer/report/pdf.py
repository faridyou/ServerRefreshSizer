"""PDF report via xhtml2pdf (pure Python). On failure: log a warning, keep the HTML, never fail the run."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("refreshsizer.report")


def write_pdf(ctx: dict[str, Any], out: Path) -> Path | None:
    try:
        from xhtml2pdf import pisa

        from refreshsizer.report.html import render_html

        html = render_html(ctx, pdf=True)
        with open(out, "wb") as fh:
            status = pisa.CreatePDF(html, dest=fh, encoding="utf-8")
        if status.err:
            raise RuntimeError(f"xhtml2pdf reported {status.err} error(s)")
        return Path(out)
    except Exception as e:  # never fail the run because of the PDF engine
        log.warning("PDF generation failed (%s: %s); HTML report kept", type(e).__name__, e)
        try:
            Path(out).unlink(missing_ok=True)
        except OSError:
            pass
        return None
