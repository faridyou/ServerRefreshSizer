from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from refreshsizer.benchmarks import spec_fetch
from refreshsizer.benchmarks.cache import BenchCache
from refreshsizer.benchmarks.cpu_matcher import match_cpu, normalize_cpu_string
from refreshsizer.benchmarks.curated_db import build
from refreshsizer.benchmarks.ibm_rperf import rperf_factor, rperf_per_core
from refreshsizer.benchmarks.spc import resolve_spc
from refreshsizer.benchmarks.spec_parse import parse_result_csv
from refreshsizer.catalog.dimm_rules import choose_dimms, is_balanced
from refreshsizer.catalog.loader import legacy_model_families, load_cpus, load_platforms, oracle_core_factor
from refreshsizer.model.entities import Confidence

HTTP = Path(__file__).parent / "fixtures" / "http"


@pytest.fixture()
def cache(tmp_path: Path) -> BenchCache:
    return build(BenchCache(tmp_path / "b.sqlite"))


def test_curated_db(cache: BenchCache) -> None:
    assert cache.count("curated") >= 26
    assert all(r.cpu_model_id for r in cache.query(suite="cpu2017"))


@pytest.mark.parametrize("raw,expected", [
    ("Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz", "intel-xeon-e5-2680-v4"),
    ("Intel(R) Xeon(R) Gold 6230 CPU @ 2.10GHz", "intel-xeon-gold-6230"),
    ("AMD EPYC 9555 64-Core Processor", "amd-epyc-9555"),
    ("2x Xeon Gold 6230 (20c)", "intel-xeon-gold-6230"),
    ("Xeon E5-2680v3", "intel-xeon-e5-2680-v3"),
    ("Intel Xeon 6787P", "intel-xeon-6787p"),
    ("EPYC 9175F", "amd-epyc-9175f"),
    ("Power S924", "ibm-power9"),
    ("Intel Xeon Gold 6438Y+", "intel-xeon-gold-6438y-plus"),
])
def test_match(raw: str, expected: str) -> None:
    m = match_cpu(raw)
    assert m is not None and m.cpu_id == expected


def test_normalize() -> None:
    assert normalize_cpu_string("Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz") == "Intel Xeon E5-2680 v4"
    assert normalize_cpu_string("AMD EPYC 9555 64-Core Processor") == "AMD EPYC 9555"


def test_spc_chain(cache: BenchCache) -> None:
    r = resolve_spc("Intel Xeon E5-2680 v4", chips=2, cache=cache)
    assert r.method == "exact" and abs(r.spc - 149 / 28) < 1e-9 and r.confidence == Confidence.HIGH
    r = resolve_spc("amd-epyc-9355", chips=2, cache=cache)
    assert abs(r.spc * 64 - 964) < 1e-9
    r = resolve_spc("amd-epyc-9355", chips=1, cache=cache)
    assert r.method == "scaled_chips" and r.flags
    r = resolve_spc("Intel Xeon E5-2690 v4", chips=2, cache=cache)
    assert r.method == "sibling" and abs(r.spc - 149 / 28 * 2.6 / 2.4) < 1e-9
    r = resolve_spc("Intel Xeon Gold 6130", cache=cache)
    assert r.method == "generation_avg" and r.confidence == Confidence.LOW
    r = resolve_spc("Mystery CPU 3000", cache=cache)
    assert r.method == "default" and r.confidence == Confidence.VERY_LOW
    r = resolve_spc("Mystery CPU 3000", cache=cache, user_spc={"Mystery CPU 3000": 7.5})
    assert r.method == "user" and r.spc == 7.5


def test_rperf() -> None:
    i = rperf_per_core("Power S824", 8)
    assert i and abs(i.rperf_per_core - 397.3 / 24) < 1e-9
    i = rperf_per_core("Power E980", 4)
    assert i and i.smt_used == 8 and i.flags
    f, matched, _ = rperf_factor("POWER9")
    assert f == 0.425 and matched == 0.365
    f, _, _ = rperf_factor("POWER9", mode="generation_matched")
    assert f == 0.365


def test_catalog() -> None:
    cpus = load_cpus()
    assert cpus["amd-epyc-9355"].cores == 32 and cpus["intel-xeon-6740e"].cores == 96
    plats = load_platforms()
    assert set(plats) >= {"R670", "R770", "R770AP", "R470", "R570", "R6715", "R7715", "R6725", "R7725"}
    for p in plats.values():
        for cid in p.supported_cpu_ids:
            assert cid in cpus, (p.model, cid)
    assert oracle_core_factor("Intel Xeon Gold 6230")[0] == 0.5
    assert oracle_core_factor("IBM POWER9")[0] == 1.0
    assert oracle_core_factor("Unknown")[0] == 1.0
    assert legacy_model_families("ProLiant DL380 Gen9") == ["Xeon E5 v3", "Xeon E5 v4"]
    assert legacy_model_families("ProLiant DL380 Gen10 Plus") == ["Xeon Scalable 3"]


def test_dimms() -> None:
    p = load_platforms()["R7725"]
    d = choose_dimms(p, 2, 32, "Zen5", 6400, 2136)
    assert d and d.dimm_count == 24 and d.dimm_size_gb == 96 and d.dpc == 1
    assert is_balanced(d, p, 2)
    r770 = load_platforms()["R770"]
    d2 = choose_dimms(r770, 2, 86, "P", 6400, 5000)
    assert d2 and d2.dpc == 2 and d2.speed_mt_s == 5200 and d2.dimm_count == 32
    e = choose_dimms(r770, 2, 96, "E", 6400, 3000)
    assert e is None  # E-cores limited to 64 GB DIMMs -> max 2 TB


def test_parse_result_csv() -> None:
    r = parse_result_csv((HTTP / "cpu2017-20250115-46001.csv").read_text(), "https://x/cpu2017-20250115-46001.csv")
    assert r and r.value_base == 970 and r.enabled_cores == 64 and r.chips == 2
    assert r.cpu_model_id == "amd-epyc-9355" and r.source == "live" and r.value_peak is None


def _transport() -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        assert req.headers["User-Agent"].startswith("RefreshSizer/1.0")
        if url.endswith("/cpu2017/results/"):
            return httpx.Response(200, text=(HTTP / "cpu2017_index.html").read_text(), headers={"ETag": '"a"'})
        name = url.rstrip("/").rsplit("/", 1)[-1]
        f = HTTP / (name + ".html" if name.startswith("res") else name)
        if f.exists():
            return httpx.Response(200, text=f.read_text())
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_fetch_recorded(cache: BenchCache) -> None:
    rep = spec_fetch.update("cpu2017", transport=_transport(), cache=cache, min_interval_s=0, force=True)
    assert rep.online and rep.results_added == 1
    live = [r for r in cache.query("amd-epyc-9355") if r.source == "live"]
    assert live and live[0].value_base == 970
    r = resolve_spc("amd-epyc-9355", chips=2, system_hint="R7725", cache=cache)
    assert r.results[0].source == "live" and abs(r.spc - 970 / 64) < 1e-9  # live overrides curated
    rep2 = spec_fetch.update("cpu2017", transport=_transport(), cache=cache, min_interval_s=0)
    assert rep2.skipped_recent


def test_fetch_network_failure(cache: BenchCache) -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("proxy refused")
    rep = spec_fetch.update("cpu2017", transport=httpx.MockTransport(boom), cache=cache, min_interval_s=0, force=True)
    assert not rep.online and "curated" in rep.messages[0]
    assert cache.count("curated") >= 26


def test_offline_mode(cache: BenchCache) -> None:
    rep = spec_fetch.update("cpu2017", offline=True, cache=cache)
    assert not rep.online and rep.messages
