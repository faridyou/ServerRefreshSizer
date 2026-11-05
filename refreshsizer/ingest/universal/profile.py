"""Deterministic workbook profiling: header rows (incl. merged multi-row), tables, transposed tables,
subtotal/notes removal, type and unit inference (section 4.6 A)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from refreshsizer.ingest.common import CSV_EXT, read_csv_any, to_float

TOTAL_RE = re.compile(r"^\s*(grand\s+)?(sous[\s-]?total|sub[\s-]?total|total|totaux|somme|sum|average|moyenne|avg)\b",
                      re.I)
UNIT_PATTERNS = [
    (re.compile(r"\b(kib|ko|kb)\b", re.I), "KiB"),
    (re.compile(r"\b(mib|mo|mb)\b(?!\s*/\s*s)", re.I), "MiB"),
    (re.compile(r"\b(gib|go|gb)\b", re.I), "GiB"),
    (re.compile(r"\b(tib|to|tb)\b", re.I), "TiB"),
    (re.compile(r"\bghz\b", re.I), "GHz"),
    (re.compile(r"\bmhz\b", re.I), "MHz"),
    (re.compile(r"%|\bpct\b|\bpercent", re.I), "pct"),
    (re.compile(r"mb\s*/\s*s|mbps", re.I), "MB/s"),
]


class ColumnProfile(BaseModel):
    name: str
    index: int
    dtype: str = "text"  # numeric | text | percent | bool | empty
    unit: str | None = None
    samples: list[str] = Field(default_factory=list)
    null_pct: float = 0.0
    distinct: int = 0


class TableProfile(BaseModel):
    file: str
    sheet: str
    table_index: int = 0
    header_rows: list[int] = Field(default_factory=list)
    columns: list[ColumnProfile] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    transposed: bool = False
    dropped: list[str] = Field(default_factory=list)
    totals: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def table_id(self) -> str:
        return f"{self.file}::{self.sheet}::{self.table_index}"


class WorkbookProfile(BaseModel):
    file: str
    sheets: list[str] = Field(default_factory=list)
    tables: list[TableProfile] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _cell(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return v


def _grids(path: Path) -> dict[str, list[list[Any]]]:
    ext = path.suffix.lower()
    if ext in CSV_EXT:
        df = read_csv_any(path, header=None)
        return {path.stem: [[_cell(v) for v in row] for row in df.itertuples(index=False)]}
    if ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        wb = load_workbook(path, data_only=True)
        out: dict[str, list[list[Any]]] = {}
        for ws in wb.worksheets:
            grid = [[_cell(c) for c in row] for row in ws.iter_rows(values_only=True)]
            for rng in ws.merged_cells.ranges:
                anchor = grid[rng.min_row - 1][rng.min_col - 1] if rng.min_row - 1 < len(grid) else None
                for r in range(rng.min_row - 1, min(rng.max_row, len(grid))):
                    for c in range(rng.min_col - 1, rng.max_col):
                        if c < len(grid[r]):
                            grid[r][c] = anchor
            out[ws.title] = grid
        return out
    engine = "xlrd" if ext == ".xls" else "odf" if ext == ".ods" else None
    sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=object, engine=engine)
    return {str(k): [[_cell(v) for v in row] for row in df.itertuples(index=False)] for k, df in sheets.items()}


def _is_num(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    s = str(v).strip()
    return bool(re.fullmatch(r"[-+]?[\d\s.,']+\s*(%|go|gb|gib|mo|mb|mib|to|tb|tib|ghz|mhz)?", s, re.I)) and \
        to_float(s) is not None


def _text_ratio(row: list[Any]) -> tuple[float, int]:
    vals = [v for v in row if v is not None]
    if not vals:
        return 0.0, 0
    txt = sum(1 for v in vals if not _is_num(v))
    return txt / len(vals), len(vals)


def _blocks(grid: list[list[Any]]) -> list[tuple[int, list[list[Any]]]]:
    blocks: list[tuple[int, list[list[Any]]]] = []
    cur: list[list[Any]] = []
    start = 0
    for i, row in enumerate(grid):
        if all(v is None for v in row):
            if cur:
                blocks.append((start, cur))
                cur = []
            continue
        if not cur:
            start = i
        cur.append(list(row))
    if cur:
        blocks.append((start, cur))
    return blocks


def guess_unit(header: str, samples: list[str]) -> str | None:
    for rx, unit in UNIT_PATTERNS:
        if rx.search(header):
            return unit
    for s in samples:
        for rx, unit in UNIT_PATTERNS:
            if rx.search(s) and unit != "MB/s":
                return unit
    return None


def _profile_columns(header: list[str], rows: list[list[Any]]) -> list[ColumnProfile]:
    cols = []
    for j, h in enumerate(header):
        vals = [r[j] if j < len(r) else None for r in rows]
        non = [v for v in vals if v is not None]
        samples = [str(v) for v in non[:5]]
        if not non:
            dtype = "empty"
        elif all(isinstance(v, bool) or str(v).lower() in ("true", "false", "yes", "no", "oui", "non") for v in non):
            dtype = "bool"
        elif all(_is_num(v) for v in non):
            dtype = "percent" if any("%" in str(v) for v in non) else "numeric"
        else:
            dtype = "text"
        cols.append(ColumnProfile(name=h, index=j, dtype=dtype, unit=guess_unit(h, samples), samples=samples,
                                  null_pct=round(1 - len(non) / max(len(vals), 1), 3),
                                  distinct=len({str(v) for v in non})))
    return cols


def _table_from_block(file: str, sheet: str, idx: int, start: int, block: list[list[Any]]) -> TableProfile | None:
    width = max(len(r) for r in block)
    block = [r + [None] * (width - len(r)) for r in block]
    used_cols = [j for j in range(width) if any(r[j] is not None for r in block)]
    block = [[r[j] for j in used_cols] for r in block]
    nonempty_rows = [r for r in block if any(v is not None for v in r)]
    if len(nonempty_rows) < 2:
        return None
    # transposed key/value table (fields in rows)
    if len(used_cols) == 2 and len(block) >= 3 and all(r[0] is not None and not _is_num(r[0]) for r in block) \
            and sum(1 for r in block if r[1] is not None and _is_num(r[1])) >= len(block) * 0.6:
        header = [str(r[0]) for r in block]
        row = {header[i]: block[i][1] for i in range(len(block))}
        t = TableProfile(file=file, sheet=sheet, table_index=idx, header_rows=[start], transposed=True, rows=[row])
        t.columns = _profile_columns(header, [[block[i][1] for i in range(len(block))]])
        return t
    # header row: first row (within the first 10) that is mostly text with >= 2 cells
    h_i = None
    for i, r in enumerate(block[:10]):
        ratio, n = _text_ratio(r)
        if n >= max(2, int(0.5 * len(used_cols))) and ratio >= 0.75:
            h_i = i
            break
    if h_i is None:
        return None
    header_rows = [h_i]
    hdr = [str(v) if v is not None else "" for v in block[h_i]]
    if h_i + 1 < len(block):
        nxt = block[h_i + 1]
        ratio, n = _text_ratio(nxt)
        dup = any(hdr[j] and j + 1 < len(hdr) and hdr[j] == hdr[j + 1] for j in range(len(hdr)))
        if ratio >= 0.9 and n >= 2 and dup:
            header_rows.append(h_i + 1)
            new = []
            for j in range(len(hdr)):
                p, c = hdr[j], (str(nxt[j]) if nxt[j] is not None else "")
                new.append(p if (not c or c == p) else (f"{p} / {c}" if p else c))
            hdr = new
    hdr = [h if h else f"col{j + 1}" for j, h in enumerate(hdr)]
    seen: dict[str, int] = {}
    for j, h in enumerate(hdr):
        if h in seen:
            seen[h] += 1
            hdr[j] = f"{h} ({seen[h]})"
        else:
            seen[h] = 0
    data = block[header_rows[-1] + 1:]
    t = TableProfile(file=file, sheet=sheet, table_index=idx, header_rows=[start + i for i in header_rows])
    keep: list[list[Any]] = []
    for r in data:
        vals = [v for v in r if v is not None]
        first = next((str(v) for v in r if v is not None), "")
        if TOTAL_RE.match(first):
            t.dropped.append(f"total/subtotal row: {first}")
            t.totals.append({hdr[j]: r[j] for j in range(len(hdr)) if r[j] is not None})
            continue
        if [str(v) if v is not None else "" for v in r] == [str(v) if v is not None else "" for v in block[h_i]]:
            t.dropped.append("repeated header row")
            continue
        if len(vals) == 1 and len(hdr) > 3 and isinstance(vals[0], str) and len(vals[0]) > 25:
            t.dropped.append(f"note: {vals[0][:40]}")
            continue
        keep.append(r)
    t.columns = _profile_columns(hdr, keep)
    t.rows = [{hdr[j]: r[j] for j in range(len(hdr))} for r in keep]
    return t if t.rows else None


def profile_file(path: Path) -> WorkbookProfile:
    path = Path(path)
    wp = WorkbookProfile(file=path.name)
    for sheet, grid in _grids(path).items():
        wp.sheets.append(sheet)
        idx = 0
        for start, block in _blocks(grid):
            t = _table_from_block(path.name, sheet, idx, start, block)
            if t is None:
                if len(block) <= 2:
                    wp.notes.append(f"{sheet}: row {start + 1}: title/notes block ignored")
                continue
            wp.tables.append(t)
            idx += 1
    return wp
