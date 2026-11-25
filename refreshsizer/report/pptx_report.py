"""PowerPoint (.pptx) export of the report outline (python-pptx, pure Python). 16:9, one section per slide;
content that does not fit continues on a new slide, long tables are split with their header repeated."""

from __future__ import annotations

import io
import math
import re
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

from refreshsizer.report.outline import Block, Outline, build_outline

ACCENT = RGBColor(0x1F, 0x4E, 0x79)
INK = RGBColor(0x0B, 0x0B, 0x0B)
MUTED = RGBColor(0x52, 0x51, 0x4E)
WARN = RGBColor(0x8F, 0x1D, 0x1B)
HEADER_FILL = RGBColor(0x1F, 0x4E, 0x79)
ZEBRA = RGBColor(0xF2, 0xF5, 0xF9)
W, H = Inches(13.333), Inches(7.5)
LEFT, TOP, RIGHT, BOTTOM = Inches(0.5), Inches(1.15), Inches(0.5), Inches(0.45)
CONTENT_W = W - LEFT - RIGHT
ROWS_PER_SLIDE = 12


class Deck:
    def __init__(self, ol: Outline):
        self.ol = ol
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = W, H
        self.blank = self.prs.slide_layouts[6]
        self.slide: Any = None
        self.y: int = int(TOP)
        self.title = ""

    # ---- slides -----------------------------------------------------------------------------------
    def new_slide(self, title: str) -> None:
        self.slide = self.prs.slides.add_slide(self.blank)
        title = re.sub(r"(:\s*|^)\d+\.\s+", r"\1", title)
        self.title = title
        bar = self.slide.shapes.add_shape(1, 0, 0, W, Inches(0.95))  # 1 = rectangle
        bar.fill.solid()
        bar.fill.fore_color.rgb = ACCENT
        bar.line.fill.background()
        tf = bar.text_frame
        tf.margin_left = LEFT
        tf.text = title
        p = tf.paragraphs[0]
        p.font.size, p.font.bold, p.font.color.rgb = Pt(24), True, RGBColor(0xFF, 0xFF, 0xFF)
        foot = self.slide.shapes.add_textbox(LEFT, H - Inches(0.4), CONTENT_W, Inches(0.3)).text_frame
        foot.text = f"{self.ol.title} - {self.ol.subtitle}"
        foot.paragraphs[0].font.size, foot.paragraphs[0].font.color.rgb = Pt(9), MUTED
        self.y = int(TOP)

    def room(self) -> int:
        return int(H - BOTTOM - self.y)

    def ensure(self, need: int) -> None:
        if need > self.room() and self.y > TOP:
            self.new_slide(self.title if self.title.endswith("(cont.)") else f"{self.title} (cont.)")

    # ---- blocks -----------------------------------------------------------------------------------
    def text(self, text: str, size: int = 14, color: RGBColor = INK, bold: bool = False, italic: bool = False) -> None:
        chars_per_line = max(40, int(CONTENT_W / Pt(size) * 1.9))
        lines = max(1, math.ceil(len(text) / chars_per_line))
        h = int(Pt(size) * 1.35 * lines + Inches(0.12))
        self.ensure(h)
        box = self.slide.shapes.add_textbox(LEFT, self.y, CONTENT_W, h)
        tf = box.text_frame
        tf.word_wrap = True
        tf.text = text
        f = tf.paragraphs[0].font
        f.size, f.color.rgb, f.bold, f.italic = Pt(size), color, bold, italic
        self.y += h

    def bullets(self, items: list[str], numbered: bool) -> None:
        for i, it in enumerate(items, 1):
            self.text(f"{i}. {it}" if numbered else f"• {it}", size=16)

    def kpis(self, items: list[tuple[str, str]]) -> None:
        h = Inches(1.25)
        self.ensure(h)
        gap = Inches(0.2)
        w = int((CONTENT_W - gap * (len(items) - 1)) / max(len(items), 1))
        for i, (val, label) in enumerate(items):
            box = self.slide.shapes.add_shape(1, LEFT + i * (w + gap), self.y, w, h)
            box.fill.solid()
            box.fill.fore_color.rgb = ZEBRA
            box.line.color.rgb = RGBColor(0xD4, 0xE2, 0xF3)
            tf = box.text_frame
            tf.text = str(val)
            p0 = tf.paragraphs[0]
            p0.font.size, p0.font.bold, p0.font.color.rgb = Pt(30), True, ACCENT
            p1 = tf.add_paragraph()
            p1.text = str(label)
            p1.font.size, p1.font.color.rgb = Pt(12), MUTED
        self.y += h + Inches(0.15)

    def image(self, png: bytes, caption: str = "") -> None:
        from PIL import Image

        im = Image.open(io.BytesIO(png))
        ratio = im.height / im.width
        max_h = int(H - TOP - BOTTOM - Inches(0.3))
        w = min(int(Inches(8.5)), int(CONTENT_W))
        h = int(w * ratio)
        if h > max_h:
            h, w = max_h, int(max_h / ratio)
        self.ensure(h)
        if h > self.room():
            h = self.room()
            w = int(h / ratio)
        self.slide.shapes.add_picture(io.BytesIO(png), LEFT, self.y, width=Emu(w), height=Emu(h))
        self.y += h + Inches(0.1)
        if caption:
            self.text(caption, size=11, color=MUTED)

    def table(self, b: Block) -> None:
        has_header = any(h for h in b.header)
        ncols = max([len(b.header)] + [len(r) for r in b.rows]) if (b.header or b.rows) else 0
        if not ncols:
            return
        size = 16 if ncols <= 3 else 14 if ncols <= 5 else 12 if ncols <= 7 else 11
        row_h = int(Pt(size) * 2.0)
        rows = b.rows or [[""] * ncols]
        lens = [max([len(str(b.header[j])) if j < len(b.header) else 0]
                    + [len(str(r[j])) if j < len(r) else 0 for r in rows]) for j in range(ncols)]
        lens = [min(max(x, 9), 55) for x in lens]
        widths = [int(CONTENT_W * x / sum(lens)) for x in lens]
        start = 0
        while start < len(rows):
            fit = max(1, min(ROWS_PER_SLIDE, int((self.room() - row_h * (1 if has_header else 0)) / row_h)))
            if fit < 3 and self.y > TOP:
                self.new_slide(self.title if self.title.endswith("(cont.)") else f"{self.title} (cont.)")
                continue
            chunk = rows[start:start + fit]
            n = len(chunk) + (1 if has_header else 0)
            shape = self.slide.shapes.add_table(n, ncols, LEFT, self.y, int(CONTENT_W), row_h * n)
            tbl = shape.table
            tbl.first_row = has_header
            for j, w in enumerate(widths):
                tbl.columns[j].width = w
            all_rows = ([b.header] if has_header else []) + chunk
            for i, row in enumerate(all_rows):
                for j in range(ncols):
                    cell = tbl.cell(i, j)
                    cell.text = str(row[j]) if j < len(row) else ""
                    cell.margin_top = cell.margin_bottom = Inches(0.03)
                    para = cell.text_frame.paragraphs[0]
                    para.font.size = Pt(size)
                    if j < len(b.align) and b.align[j] == "r":
                        para.alignment = PP_ALIGN.RIGHT
                    cell.fill.solid()
                    if has_header and i == 0:
                        cell.fill.fore_color.rgb = HEADER_FILL
                        para.font.bold, para.font.color.rgb = True, RGBColor(0xFF, 0xFF, 0xFF)
                    else:
                        cell.fill.fore_color.rgb = ZEBRA if i % 2 else RGBColor(0xFF, 0xFF, 0xFF)
                        para.font.color.rgb = INK
            self.y += row_h * n + Inches(0.15)
            start += len(chunk)
            if start < len(rows):
                self.new_slide(self.title if self.title.endswith("(cont.)") else f"{self.title} (cont.)")

    def render(self, b: Block) -> None:
        if b.kind == "heading":
            self.text(b.text, size=16, color=ACCENT, bold=True)
        elif b.kind == "para":
            style = {"lead": (20, INK, False, False), "muted": (13, MUTED, False, False),
                     "note": (18, ACCENT, False, True), "warn": (16, WARN, True, False)}.get(b.style, (16, INK, False, False))
            self.text(b.text, size=style[0], color=style[1], bold=style[2], italic=style[3])
        elif b.kind == "kpis":
            self.kpis(b.items)
        elif b.kind == "table":
            self.table(b)
        elif b.kind == "image" and b.png:
            self.image(b.png, b.text)
        elif b.kind == "bullets":
            self.bullets([str(x) for x in b.items], b.numbered)
        elif b.kind == "pre":
            if b.text:
                self.text(b.text, size=12, bold=True)
            for line in b.items:
                self.text(str(line), size=10)


def write_pptx(ctx: dict[str, Any], out: Path, outline: Outline | None = None) -> Path:
    ol = outline or build_outline(ctx)
    deck = Deck(ol)
    # title slide
    s = deck.prs.slides.add_slide(deck.blank)
    bg = s.shapes.add_shape(1, 0, 0, W, H)
    bg.fill.solid()
    bg.fill.fore_color.rgb = ACCENT
    bg.line.fill.background()
    tb = s.shapes.add_textbox(Inches(0.8), Inches(2.6), Inches(11.5), Inches(2.2)).text_frame
    tb.word_wrap = True
    tb.text = ol.title
    tb.paragraphs[0].font.size, tb.paragraphs[0].font.bold = Pt(40), True
    tb.paragraphs[0].font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    p = tb.add_paragraph()
    p.text = ol.subtitle
    p.font.size, p.font.color.rgb = Pt(18), RGBColor(0xDD, 0xE7, 0xF3)
    if ol.lead:
        lead = s.shapes.add_textbox(Inches(0.8), Inches(4.9), Inches(11.5), Inches(1.8)).text_frame
        lead.word_wrap = True
        lead.text = ol.lead
        lead.paragraphs[0].font.size, lead.paragraphs[0].font.color.rgb = Pt(16), RGBColor(0xFF, 0xFF, 0xFF)
    for sec in ol.sections:
        if not sec.slide:
            continue
        deck.new_slide(sec.title)
        for b in sec.blocks:
            deck.render(b)
    if ol.footer:
        deck.new_slide("Notes")
        deck.text(ol.footer, size=16, color=MUTED)
    deck.prs.core_properties.title = ol.title
    deck.prs.core_properties.author = "RefreshSizer"
    deck.prs.save(str(out))
    return Path(out)
