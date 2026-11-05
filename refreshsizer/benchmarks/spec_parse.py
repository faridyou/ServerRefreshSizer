"""Parsers for spec.org CPU2017/CPU2026 result CSV files and quarterly index pages."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from refreshsizer.benchmarks.cpu_matcher import match_cpu
from refreshsizer.model.entities import BenchmarkResult

METRIC_RE = re.compile(r"SPEC(rate|speed)(2017|2026)_(int|fp)_(base|peak)", re.I)


def _num(s: str | None) -> float | None:
    if s is None:
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s.replace(",", ""))
    return float(m.group(0)) if m else None


def parse_result_csv(text: str, url: str, retrieved_at: str | None = None) -> BenchmarkResult | None:
    """Parse one SPEC CPU result CSV. Returns None for non-rate or invalid results."""
    kv: dict[str, str] = {}
    metrics: dict[str, float | None] = {}
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        key = row[0].strip().strip('"')
        val = row[1].strip() if len(row) > 1 else ""
        m = METRIC_RE.fullmatch(key)
        if m:
            metrics[key] = _num(val)
            continue
        k = key.rstrip(":").strip().lower()
        if k and k not in kv:
            kv[k] = val
    rate = {k: v for k, v in metrics.items() if "rate" in k.lower()}
    if not rate:
        return None
    base_key = next((k for k in rate if k.lower().endswith("_base")), None)
    peak_key = next((k for k in rate if k.lower().endswith("_peak")), None)
    if base_key is None or rate.get(base_key) is None:
        return None
    if kv.get("valid", "1").strip() in ("0", "invalid"):
        return None
    mm = METRIC_RE.fullmatch(base_key)
    assert mm
    suite = f"cpu{mm.group(2)}"
    enabled = kv.get("enabled", "")
    cores = _num(re.search(r"(\d+)\s*cores?", enabled).group(1)) if re.search(r"(\d+)\s*cores?", enabled) else None
    chips = _num(re.search(r"(\d+)\s*chips?", enabled).group(1)) if re.search(r"(\d+)\s*chips?", enabled) else None
    tpc = _num(re.search(r"(\d+)\s*threads?\s*/\s*core", enabled).group(1)) \
        if re.search(r"(\d+)\s*threads?\s*/\s*core", enabled) else None
    cpu_name = kv.get("cpu name", "")
    match = match_cpu(cpu_name)
    rid = re.sub(r"\.csv$", "", url.rsplit("/", 1)[-1])
    system = kv.get("model name") or kv.get("system name") or ""
    return BenchmarkResult(
        id=rid, suite=suite, metric=base_key, value_base=rate.get(base_key),
        value_peak=rate.get(peak_key) if peak_key else None,
        system_vendor=kv.get("hardware vendor"), system_name=system, cpu_name=cpu_name,
        cpu_model_id=match.cpu_id if match else None, chips=int(chips) if chips else None,
        enabled_cores=int(cores) if cores else None, threads_per_core=int(tpc) if tpc else None,
        memory_desc=kv.get("memory"), test_date=kv.get("test date"), hw_avail=kv.get("hardware availability"),
        sponsor=kv.get("test sponsor"), software=kv.get("operating system"), result_url=url,
        retrieved_at=retrieved_at or datetime.now().isoformat(timespec="seconds"), source="live",
    )


@dataclass
class IndexEntry:
    csv_url: str
    text: str


def parse_results_index(html: str, base_url: str) -> list[str]:
    """Top-level results page -> quarterly page URLs (newest first)."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if re.search(r"res\d{4}q[1-4]/?$", href):
            out.append(urljoin(base_url, href if href.endswith("/") else href + "/"))
    out = list(dict.fromkeys(out))

    def key(u: str) -> tuple[int, int]:
        m = re.search(r"res(\d{4})q([1-4])", u)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

    return sorted(out, key=key, reverse=True)


def parse_quarter_page(html: str, base_url: str, rate_only: bool = True) -> list[IndexEntry]:
    """Quarterly page -> CSV links of Integer/FP Rate results (row text kept for CPU filtering)."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[IndexEntry] = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if not href.lower().endswith(".csv"):
            continue
        row = a.find_parent("tr")
        text = row.get_text(" ", strip=True) if row else ""
        section = a.find_previous(["h2", "h3", "caption"])
        sec = section.get_text(" ", strip=True).lower() if section else ""
        if rate_only and sec and "rate" not in sec:
            continue
        out.append(IndexEntry(urljoin(base_url, href), text))
    return out
