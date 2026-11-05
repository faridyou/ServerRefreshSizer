"""SQLite benchmark cache (stdlib sqlite3, no ORM). File: data/benchmarks.sqlite."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from refreshsizer.model.entities import BenchmarkResult
from refreshsizer.paths import bench_db_path

FIELDS = list(BenchmarkResult.model_fields.keys())

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS results (
  {", ".join(f"{f} TEXT" if f != "id" else "id TEXT PRIMARY KEY" for f in FIELDS)}
);
CREATE INDEX IF NOT EXISTS idx_results_cpu ON results(cpu_model_id, suite);
CREATE TABLE IF NOT EXISTS http_cache (url TEXT PRIMARY KEY, etag TEXT, last_modified TEXT,
  fetched_at TEXT, status INTEGER, body BLOB);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS fetch_log (ts TEXT, level TEXT, message TEXT);
"""

NUMERIC = {"value_base", "value_peak"}
INTS = {"chips", "enabled_cores", "threads_per_core", "smt_mode"}


class BenchCache:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else bench_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---- results --------------------------------------------------------
    def upsert(self, results: list[BenchmarkResult], overwrite: bool = True) -> int:
        verb = "INSERT OR REPLACE" if overwrite else "INSERT OR IGNORE"
        sql = f"{verb} INTO results ({', '.join(FIELDS)}) VALUES ({', '.join('?' for _ in FIELDS)})"
        rows = []
        for r in results:
            d = r.model_dump()
            rows.append(tuple(None if d[f] is None else str(d[f]) for f in FIELDS))
        with self.conn:
            self.conn.executemany(sql, rows)
        return len(rows)

    def _row(self, row: tuple[Any, ...]) -> BenchmarkResult:
        d: dict[str, Any] = {}
        for f, v in zip(FIELDS, row, strict=True):
            if v is None:
                d[f] = None
            elif f in NUMERIC:
                d[f] = float(v)
            elif f in INTS:
                d[f] = int(float(v))
            else:
                d[f] = v
        return BenchmarkResult(**{k: v for k, v in d.items() if v is not None or k == "id"})

    def query(self, cpu_model_id: str | None = None, suite: str | None = None,
              metric: str | None = None) -> list[BenchmarkResult]:
        sql = f"SELECT {', '.join(FIELDS)} FROM results WHERE 1=1"
        args: list[Any] = []
        if cpu_model_id:
            sql += " AND cpu_model_id = ?"
            args.append(cpu_model_id)
        if suite:
            sql += " AND suite = ?"
            args.append(suite)
        if metric:
            sql += " AND metric = ?"
            args.append(metric)
        return [self._row(r) for r in self.conn.execute(sql, args).fetchall()]

    def search(self, text: str) -> list[BenchmarkResult]:
        like = f"%{text.lower()}%"
        sql = (f"SELECT {', '.join(FIELDS)} FROM results WHERE lower(cpu_name) LIKE ? OR lower(cpu_model_id) LIKE ?"
               " OR lower(system_name) LIKE ?")
        return [self._row(r) for r in self.conn.execute(sql, (like, like, like)).fetchall()]

    def count(self, source: str | None = None) -> int:
        if source:
            return int(self.conn.execute("SELECT COUNT(*) FROM results WHERE source=?", (source,)).fetchone()[0])
        return int(self.conn.execute("SELECT COUNT(*) FROM results").fetchone()[0])

    # ---- meta / log -----------------------------------------------------
    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value: Any) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def log(self, level: str, message: str) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO fetch_log VALUES (?, ?, ?)",
                              (datetime.now().isoformat(timespec="seconds"), level, message))

    # ---- HTTP cache -----------------------------------------------------
    def http_get(self, url: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT etag, last_modified, fetched_at, status, body FROM http_cache WHERE url=?",
                                (url,)).fetchone()
        if not row:
            return None
        return {"etag": row[0], "last_modified": row[1], "fetched_at": row[2], "status": row[3], "body": row[4]}

    def http_put(self, url: str, etag: str | None, last_modified: str | None, status: int, body: bytes) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO http_cache VALUES (?, ?, ?, ?, ?, ?)",
                              (url, etag, last_modified, datetime.now().isoformat(timespec="seconds"), status, body))
