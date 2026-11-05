"""Shared helpers for all parsers: workbook reading, tolerant column matching, value coercion."""

from __future__ import annotations

import csv
import io
import logging
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger("refreshsizer.ingest")

SPREADSHEET_EXT = {".xlsx", ".xlsm", ".xls", ".ods"}
CSV_EXT = {".csv", ".txt", ".tsv"}


def norm_header(h: Any) -> str:
    """Normalize a header for matching: lower case, no accents, units/punctuation collapsed."""
    s = "" if h is None else str(h)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("œ", "oe").replace("Œ", "oe").lower().strip()
    s = s.replace("#", " num ").replace("%", " pct ")
    s = re.sub(r"[_\-/\\.:,;]+", " ", s)
    s = re.sub(r"[()\[\]{}]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def norm_header_nounit(h: Any) -> str:
    s = norm_header(h)
    s = re.sub(r"\b(mib|gib|tib|kib|mb|gb|tb|go|to|mo|mhz|ghz|ms|mb s|mbps)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def find_col(columns: Iterable[Any], *candidates: str) -> str | None:
    """Return the actual column whose normalized header matches one of the candidates."""
    cols = list(columns)
    normed = {norm_header(c): c for c in cols}
    for cand in candidates:
        n = norm_header(cand)
        if n in normed:
            return normed[n]
    nounit = {norm_header_nounit(c): c for c in cols}
    for cand in candidates:
        n = norm_header_nounit(cand)
        if n and n in nounit:
            return nounit[n]
    return None


def detect_encoding_and_sep(raw: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 never fails
        enc, text = "latin-1", raw.decode("latin-1")
    sample = "\n".join(text.splitlines()[:30])
    try:
        sep = csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
    except csv.Error:
        counts = {s: sample.count(s) for s in (";", ",", "\t", "|")}
        sep = max(counts, key=lambda k: counts[k])
    return enc, sep


def read_csv_any(path: Path, header: int | None = 0) -> pd.DataFrame:
    raw = Path(path).read_bytes()
    enc, sep = detect_encoding_and_sep(raw)
    text = raw.decode(enc)
    return pd.read_csv(io.StringIO(text), sep=sep, header=header, dtype=str, keep_default_na=False,
                       engine="python")


def read_workbook(path: Path, header: int | None = 0) -> dict[str, pd.DataFrame]:
    """Read every sheet (or the CSV) as string-typed DataFrames."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in CSV_EXT:
        return {path.stem: read_csv_any(path, header=header)}
    engine = None
    if ext == ".xls":
        engine = "xlrd"
    elif ext == ".ods":
        engine = "odf"
    elif ext in (".xlsx", ".xlsm"):
        engine = "openpyxl"
    sheets = pd.read_excel(path, sheet_name=None, header=header, dtype=object, engine=engine)
    out: dict[str, pd.DataFrame] = {}
    for name, df in sheets.items():
        df = df.dropna(how="all")
        if header is not None:
            keep = [c for c in df.columns
                    if not (str(c).startswith("Unnamed:") and df[c].isna().all())]
            df = df[keep]
            df.columns = [str(c).strip() for c in df.columns]
        else:
            df = df.dropna(axis=1, how="all")
        out[str(name)] = df
    return out


def sheet_names(path: Path) -> list[str]:
    path = Path(path)
    ext = path.suffix.lower()
    if ext in CSV_EXT:
        return [path.stem]
    if ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True)
        names = list(wb.sheetnames)
        wb.close()
        return names
    return list(read_workbook(path).keys())


_NUM_RE = re.compile(
    r"[-+]?(?:\d{1,3}(?:[\s\u00a0\u202f']\d{3})+|\d+)(?:[.,]\d+)*(?:[eE][-+]?\d+)?|[-+]?[.,]\d+")


def to_float(v: Any) -> float | None:
    """Tolerant numeric parse: '2,4' -> 2.4, '1 234,5' -> 1234.5, '1,234.5' -> 1234.5, '55 %' -> 55."""
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        f = float(v)
        return None if math.isnan(f) else f
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "n/a", "na", "-", "null", "#n/a", "?"):
        return None
    m = _NUM_RE.search(s)
    if not m:
        return None
    tok = m.group(0).replace(" ", "").replace(" ", "").replace("'", "").replace(" ", "")
    if tok.count(".") > 1:
        tok = tok.replace(".", "")
    if tok.count(",") > 1:
        tok = tok.replace(",", "")
    if "," in tok and "." in tok:
        if tok.rfind(",") > tok.rfind("."):
            tok = tok.replace(".", "").replace(",", ".")
        else:
            tok = tok.replace(",", "")
    elif "," in tok:
        parts = tok.split(",")
        if len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3 and parts[0] not in ("0", "-0"):
            # ambiguous '1,234' -> thousands separator
            tok = tok.replace(",", "")
        else:
            tok = tok.replace(",", ".")
    try:
        return float(tok)
    except ValueError:
        return None


def to_int(v: Any) -> int | None:
    f = to_float(v)
    return None if f is None else int(round(f))


def to_bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v) if not (isinstance(v, float) and math.isnan(v)) else default
    s = str(v).strip().lower()
    if s in ("true", "yes", "y", "1", "oui", "vrai", "x", "poweredon", "on", "running"):
        return True
    if s in ("false", "no", "n", "0", "non", "faux", "poweredoff", "off", "suspended", "stopped", ""):
        return False if s else default
    return default


def to_str(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    s = str(v).strip()
    return s or None


def pct_value(v: Any) -> float | None:
    """Return a percentage on the 0-100 scale; accepts 0-1 fractions ('0.55') and '55 %'."""
    f = to_float(v)
    if f is None:
        return None
    s = str(v)
    if "%" not in s and 0 < f <= 1.0 and not isinstance(v, int):
        return round(f * 100.0, 9)
    return f


def row_get(row: pd.Series, col: str | None) -> Any:
    if col is None:
        return None
    v = row.get(col)
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


class ColumnMapper:
    """Map logical names to actual columns of one sheet and remember what stayed unmapped."""

    def __init__(self, df: pd.DataFrame, spec: Mapping[str, tuple[str, ...]], sheet: str, file: str):
        self.df = df
        self.sheet = sheet
        self.file = file
        self.cols: dict[str, str | None] = {k: find_col(df.columns, *v) for k, v in spec.items()}
        used = {c for c in self.cols.values() if c}
        self.unmapped = [str(c) for c in df.columns if c not in used]
        if self.unmapped:
            log.info("%s/%s: unmapped columns: %s", Path(file).name, sheet, ", ".join(self.unmapped[:30]))

    def get(self, row: pd.Series, key: str) -> Any:
        return row_get(row, self.cols.get(key))

    def has(self, key: str) -> bool:
        return self.cols.get(key) is not None

    def mapping_entries(self) -> list[dict[str, Any]]:
        out = []
        for k, c in self.cols.items():
            if c is None:
                continue
            sample = None
            if len(self.df):
                sample = to_str(self.df.iloc[0].get(c))
            out.append({"file": Path(self.file).name, "sheet": self.sheet, "column": c, "target": k,
                        "confidence": 1.0, "method": "parser", "sample": sample})
        return out
