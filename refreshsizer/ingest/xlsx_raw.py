"""Fallback .xlsx reader (stdlib only) for files openpyxl refuses to open.

Some generators (e.g. Live Optics' Aging Infrastructure Report) write stylesheets openpyxl rejects. The data
is still standard SpreadsheetML, so we read workbook.xml, the sheet XML, shared and inline strings directly.
Values only: no styles, no merged cells, no formulas."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

_REF = re.compile(r"([A-Z]+)(\d+)")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr(el: ET.Element, name: str) -> str | None:
    for k, v in el.attrib.items():
        if _local(k) == name:
            return v
    return None


def _col(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _text(el: ET.Element) -> str:
    return "".join(t.text or "" for t in el.iter() if _local(t.tag) == "t")


def read_xlsx_grids(path: Path) -> dict[str, list[list[Any]]]:
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = [_text(si) for si in root if _local(si.tag) == "si"]
        rels: dict[str, str] = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
                target = r.attrib.get("Target", "")
                target = target.lstrip("/") if target.startswith("/") else str(PurePosixPath("xl") / target)
                rels[r.attrib.get("Id", "")] = target
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        out: dict[str, list[list[Any]]] = {}
        sheets = [el for el in wb.iter() if _local(el.tag) == "sheet"]
        for i, sh in enumerate(sheets, 1):
            name = sh.attrib.get("name", f"Sheet{i}")
            target = rels.get(_attr(sh, "id") or "", f"xl/worksheets/sheet{i}.xml")
            if target not in names:
                continue
            grid: dict[int, dict[int, Any]] = {}
            for row in ET.fromstring(z.read(target)).iter():
                if _local(row.tag) != "row":
                    continue
                r_idx = int(row.attrib.get("r", len(grid) + 1)) - 1
                cells: dict[int, Any] = {}
                for j, c in enumerate(el for el in row if _local(el.tag) == "c"):
                    m = _REF.match(c.attrib.get("r", ""))
                    col = _col(m.group(1)) if m else j
                    t = c.attrib.get("t", "n")
                    v_el = next((el for el in c if _local(el.tag) == "v"), None)
                    raw = v_el.text if v_el is not None else None
                    if t == "s" and raw is not None:
                        val: Any = shared[int(raw)] if int(raw) < len(shared) else None
                    elif t == "inlineStr":
                        val = _text(c)
                    elif t in ("str", "e"):
                        val = raw
                    elif t == "b":
                        val = raw == "1"
                    elif raw is None or raw == "":
                        val = None
                    else:
                        try:
                            f = float(raw)
                            val = int(f) if f.is_integer() and "." not in raw and "E" not in raw.upper() else f
                        except ValueError:
                            val = raw
                    if isinstance(val, str) and not val.strip():
                        val = None
                    cells[col] = val
                grid[r_idx] = cells
            if not grid:
                out[name] = []
                continue
            width = max((max(c) + 1 for c in grid.values() if c), default=0)
            out[name] = [[grid.get(r, {}).get(c) for c in range(width)] for r in range(max(grid) + 1)]
        return out


def sheet_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
    return [el.attrib.get("name", "") for el in wb.iter() if _local(el.tag) == "sheet"]
