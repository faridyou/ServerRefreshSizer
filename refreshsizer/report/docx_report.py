"""Word (.docx) export of the report outline (python-docx, pure Python)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from refreshsizer.report.outline import Block, Outline, build_outline

ACCENT = RGBColor(0x1F, 0x4E, 0x79)
MUTED = RGBColor(0x52, 0x51, 0x4E)
WARN = RGBColor(0x8F, 0x1D, 0x1B)


def _shade(cell: Any, hex_fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tc_pr.append(shd)


def _table(doc: Any, b: Block) -> None:
    has_header = any(h for h in b.header)
    ncols = max([len(b.header)] + [len(r) for r in b.rows]) if (b.header or b.rows) else 0
    if not ncols:
        return
    tbl = doc.add_table(rows=0, cols=ncols)
    tbl.style = "Table Grid"
    tbl.alignment = WD_TABLE_ALIGNMENT.LEFT
    rows = ([b.header] if has_header else []) + b.rows
    for i, row in enumerate(rows):
        cells = tbl.add_row().cells
        for j in range(ncols):
            txt = str(row[j]) if j < len(row) else ""
            p = cells[j].paragraphs[0]
            run = p.add_run(txt)
            run.font.size = Pt(8.5)
            if has_header and i == 0:
                run.bold = True
                run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                _shade(cells[j], "1F4E79")
            if j < len(b.align) and b.align[j] == "r":
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    doc.add_paragraph()


def _para(doc: Any, b: Block) -> None:
    p = doc.add_paragraph()
    run = p.add_run(b.text)
    if b.style == "lead":
        run.font.size = Pt(12)
    elif b.style == "muted":
        run.font.size = Pt(8.5)
        run.font.color.rgb = MUTED
    elif b.style == "note":
        run.italic = True
        run.font.color.rgb = ACCENT
    elif b.style == "warn":
        run.bold = True
        run.font.color.rgb = WARN


def _kpis(doc: Any, b: Block) -> None:
    tbl = doc.add_table(rows=2, cols=len(b.items))
    tbl.style = "Table Grid"
    for j, (val, label) in enumerate(b.items):
        r1 = tbl.rows[0].cells[j].paragraphs[0].add_run(str(val))
        r1.bold = True
        r1.font.size = Pt(20)
        r1.font.color.rgb = ACCENT
        r2 = tbl.rows[1].cells[j].paragraphs[0].add_run(str(label))
        r2.font.size = Pt(8.5)
        r2.font.color.rgb = MUTED
    doc.add_paragraph()


def render(doc: Any, b: Block) -> None:
    if b.kind == "heading":
        doc.add_heading(b.text, level=min(b.level, 4))
    elif b.kind == "para":
        _para(doc, b)
    elif b.kind == "kpis":
        _kpis(doc, b)
    elif b.kind == "table":
        _table(doc, b)
    elif b.kind == "image" and b.png:
        doc.add_picture(io.BytesIO(b.png), width=Cm(15))
        if b.text:
            cap = doc.add_paragraph()
            r = cap.add_run(b.text)
            r.font.size = Pt(8.5)
            r.font.color.rgb = MUTED
    elif b.kind == "bullets":
        style = "List Number" if b.numbered else "List Bullet"
        for item in b.items:
            p = doc.add_paragraph(style=style)
            p.add_run(str(item)).font.size = Pt(9.5)
    elif b.kind == "pre":
        if b.text:
            doc.add_paragraph().add_run(b.text).bold = True
        for line in b.items:
            r = doc.add_paragraph().add_run(str(line))
            r.font.name = "Consolas"
            r.font.size = Pt(8)


def write_docx(ctx: dict[str, Any], out: Path, outline: Outline | None = None) -> Path:
    ol = outline or build_outline(ctx)
    doc = Document()
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Cm(2)
    sec.top_margin = sec.bottom_margin = Cm(1.8)
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10)
    t = doc.add_heading(ol.title, level=0)
    t.runs[0].font.color.rgb = ACCENT
    sub = doc.add_paragraph().add_run(ol.subtitle)
    sub.font.color.rgb = MUTED
    for s in ol.sections:
        doc.add_heading(s.title, level=1)
        for b in s.blocks:
            render(doc, b)
    if ol.footer:
        f = doc.add_paragraph().add_run(ol.footer)
        f.font.size = Pt(7.5)
        f.font.color.rgb = MUTED
    footer = sec.footer.paragraphs[0]
    footer.text = f"{ol.title} - {ol.subtitle}"
    footer.runs[0].font.size = Pt(7.5)
    doc.core_properties.title = ol.title
    doc.core_properties.author = "RefreshSizer"
    doc.save(str(out))
    return Path(out)
