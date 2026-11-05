"""Polite spec.org crawler (CPU2017 / CPU2026) with ETag cache and offline fallback.

Honours HTTP(S)_PROXY, timeout 20 s, 2 retries, <= 1 request/s per host, generic User-Agent.
Never downloads the huge "all results" page; crawls quarterly pages newest first, incrementally.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx

from refreshsizer.benchmarks.cache import BenchCache
from refreshsizer.benchmarks.curated_db import ensure_db
from refreshsizer.benchmarks.spec_parse import parse_quarter_page, parse_result_csv, parse_results_index

log = logging.getLogger("refreshsizer.bench")
USER_AGENT = "RefreshSizer/1.0 (capacity-planning tool)"
INDEX = {"cpu2017": "https://www.spec.org/cpu2017/results/", "cpu2026": "https://www.spec.org/cpu2026/results/"}


def offline_forced() -> bool:
    return os.environ.get("REFRESHSIZER_OFFLINE", "").strip() not in ("", "0", "false")


class PoliteClient:
    def __init__(self, cache: BenchCache, transport: httpx.BaseTransport | None = None,
                 min_interval_s: float = 1.0, retries: int = 2, timeout_s: float = 20.0):
        self.cache = cache
        self.retries = retries
        self.min_interval = min_interval_s
        self._last: dict[str, float] = {}
        self.client = httpx.Client(timeout=timeout_s, headers={"User-Agent": USER_AGENT}, follow_redirects=True,
                                   transport=transport, trust_env=True)

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        wait = self.min_interval - (time.monotonic() - self._last.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def get(self, url: str) -> bytes:
        cached = self.cache.http_get(url)
        headers = {}
        if cached:
            if cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
            if cached.get("last_modified"):
                headers["If-Modified-Since"] = cached["last_modified"]
        err: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle(url)
            try:
                r = self.client.get(url, headers=headers)
                if r.status_code == 304 and cached:
                    return bytes(cached["body"])
                r.raise_for_status()
                self.cache.http_put(url, r.headers.get("etag"), r.headers.get("last-modified"), r.status_code,
                                    r.content)
                return r.content
            except (httpx.HTTPError, OSError) as e:
                err = e
                log.debug("GET %s failed (attempt %d): %s", url, attempt + 1, e)
        assert err is not None
        raise err

    def close(self) -> None:
        self.client.close()


@dataclass
class UpdateReport:
    suite: str
    online: bool
    quarters_seen: int = 0
    results_added: int = 0
    skipped_recent: bool = False
    messages: list[str] = field(default_factory=list)


def update(suite: str = "cpu2017", since: str | None = None, offline: bool = False, cpu_filter: str | None = None,
           max_results: int = 200, force: bool = False, transport: httpx.BaseTransport | None = None,
           cache: BenchCache | None = None, min_interval_s: float = 1.0) -> UpdateReport:
    """Refresh live SPEC results. Falls back to the curated offline DB on any network failure."""
    cache = cache or ensure_db()
    rep = UpdateReport(suite=suite, online=False)
    if offline or (offline_forced() and transport is None):
        rep.messages.append("offline mode: using curated database only")
        cache.log("INFO", f"bench update {suite}: offline mode")
        return rep
    last = cache.get_meta(f"last_update_{suite}")
    if last and not force and datetime.fromisoformat(last) > datetime.now() - timedelta(days=7):
        rep.skipped_recent = True
        rep.messages.append(f"last update {last} < 7 days ago; use --force to refresh")
        return rep
    client = PoliteClient(cache, transport=transport, min_interval_s=min_interval_s)
    try:
        index_url = INDEX[suite]
        quarters = parse_results_index(client.get(index_url).decode("utf-8", "replace"), index_url)
        rep.online = True
        done = set(cache.get_meta(f"quarters_done_{suite}", []) or [])
        for q in quarters:
            m = re.search(r"res(\d{4})q([1-4])", q)
            tag = f"{m.group(1)}q{m.group(2)}" if m else q
            if since and tag < since.lower():
                break
            if tag in done and not force:
                continue
            rep.quarters_seen += 1
            entries = parse_quarter_page(client.get(q).decode("utf-8", "replace"), q)
            if cpu_filter:
                entries = [e for e in entries if cpu_filter.lower() in e.text.lower()]
            for e in entries:
                if rep.results_added >= max_results:
                    break
                try:
                    res = parse_result_csv(client.get(e.csv_url).decode("utf-8", "replace"), e.csv_url)
                except httpx.HTTPError as ex:
                    rep.messages.append(f"skip {e.csv_url}: {ex}")
                    continue
                if res:
                    cache.upsert([res])
                    rep.results_added += 1
            if rep.results_added < max_results:
                done.add(tag)
            if rep.results_added >= max_results:
                rep.messages.append(f"stopped after {max_results} results (use --max-results)")
                break
        cache.set_meta(f"quarters_done_{suite}", sorted(done))
        cache.set_meta(f"last_update_{suite}", datetime.now().isoformat(timespec="seconds"))
    except (httpx.HTTPError, OSError) as e:
        msg = f"spec.org unreachable ({type(e).__name__}: {e}); curated offline database kept"
        log.warning(msg)
        rep.messages.append(msg)
        cache.log("WARNING", msg)
    finally:
        client.close()
    return rep
