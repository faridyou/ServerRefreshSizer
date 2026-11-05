from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from rich.console import Console

from refreshsizer.config.schema import load_policy
from refreshsizer.engine.sizing import size_project
from refreshsizer.ingest.pipeline import ingest_files
from refreshsizer.ingest.universal.mapping_llm import LlmResponse, Redactor, llm_enabled
from refreshsizer.ingest.universal.normalize import convert, parse_composite_cpu
from refreshsizer.ingest.universal.pipeline import universal_ingest
from refreshsizer.ingest.universal.profile import profile_file
from refreshsizer.ingest.universal.review import ReviewIO
from refreshsizer.model.entities import SizingMode


def test_profile_messy(fixtures_dir: Path) -> None:
    wp = profile_file(fixtures_dir / "messy_customer_fr.xlsx")
    t = next(t for t in wp.tables if t.sheet == "Serveurs")
    assert len(t.header_rows) == 2  # merged two-row header flattened
    assert "Processeur / Type CPU" in [c.name for c in t.columns]
    assert len(t.rows) == 5 and len(t.totals) == 2  # subtotal + total dropped
    syn = next(t for t in wp.tables if t.sheet == "Synthese")
    assert syn.transposed


def test_messy_fr_mapping_and_joins(fixtures_dir: Path, tmp_path: Path) -> None:
    p = ingest_files([fixtures_dir / "messy_customer_fr.xlsx"])
    by = {c.name: c for c in p.clusters}
    assert set(by) == {"Cluster-Paris", "Cluster-Lyon"}
    h = by["Cluster-Paris"].hosts[0]
    assert h.cpu_model == "Intel Xeon Gold 6230" and h.sockets == 2 and h.cores_per_socket == 20
    assert h.nominal_ghz == 2.1 and h.ram_gb == 768 and h.cpu_util_peak_pct == 62
    assert by["Cluster-Lyon"].hosts[0].cpu_util_peak_pct == 48  # '0,48' fraction -> %
    assert sum(len(c.workloads) for c in p.clusters) == 30  # all VMs joined despite name mismatch
    assert all(w.host_id and w.host_id.startswith("SRV-ESX") for c in p.clusters for w in c.workloads)
    assert any("storage_used_tib" in w for w in p.ingest.warnings)  # 8.5 TB file vs 10.1 recomputed
    assert any("host_count" in t and "OK" in t for t in p.ingest.transformations)
    assert all(d.mode == SizingMode.SUMMARY for d in p.data_quality)


def test_semicolon_csv(fixtures_dir: Path) -> None:
    p = ingest_files([fixtures_dir / "messy_semicolon.csv"])
    h = p.clusters[0].hosts[0]
    assert h.nominal_ghz == 3.6 and h.cpu_util_p95_pct == 41.5 and h.cores == 16


def test_model_only_inventory(fixtures_dir: Path) -> None:
    p = ingest_files([fixtures_dir / "model_only_inventory.xlsx"])
    h = p.clusters[0].hosts[0]
    assert h.cpu_model == "Intel Xeon E5-2690 v4" and any("server model" in a for a in h.assumptions)
    assert p.data_quality[0].mode == SizingMode.INVENTORY_ONLY
    assert any("confirm the installed CPU" in q for q in p.customer_questions)
    run = size_project(p, load_policy())  # rules-only still gives a valid sizing with flagged gaps
    cs = run.clusters[0]
    assert cs.recommended and cs.demand.confidence.value == "VERY_LOW"
    assert any("Live Optics" in r for r in cs.recommendations)


def test_composite_and_units() -> None:
    assert parse_composite_cpu("2x Xeon Gold 6230 (20c)") == {"sockets": 2, "cpu_model": "Intel Xeon Gold 6230",
                                                              "cores_per_socket": 20}
    assert parse_composite_cpu("2 x 14 cores") == {"sockets": 2, "cores_per_socket": 14}
    assert convert("ram_gb", "512 Go", None) == 512
    assert convert("ram_gb", "2 To", None) == 2048
    assert convert("ram_gb", 524288, "MiB") == 512
    assert convert("nominal_ghz", "2.4-3.3 GHz", None) == 2.4
    assert convert("nominal_ghz", 2400, "MHz") == 2.4
    assert convert("cpu_util_peak_pct", "0,55", None) == 55


@given(st.floats(min_value=0.01, max_value=1e6, allow_nan=False))
def test_unit_roundtrip(v: float) -> None:
    gib = convert("ram_gb", v * 1024, "MiB")
    assert gib is not None and abs(gib - v) <= 1e-9 * max(1.0, v)
    tib = convert("storage_used", v / 1024, "TiB")
    assert tib is not None and abs(tib - v) <= 1e-9 * max(1.0, v)


def test_profile_reuse(fixtures_dir: Path, tmp_path: Path) -> None:
    pol = load_policy()
    universal_ingest([fixtures_dir / "messy_semicolon.csv"], policy=pol, profiles_dir=tmp_path)
    assert list(tmp_path.glob("*.json"))
    r = universal_ingest([fixtures_dir / "messy_semicolon.csv"], policy=pol, profiles_dir=tmp_path)[0]
    assert all(m.method == "profile" for m in r.mappings if m.target)
    assert len(r.hosts) == 3


def test_llm_disabled_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert not llm_enabled("auto")
    assert not llm_enabled(False)


def _unknown_headers_file(tmp_path: Path) -> Path:
    p = tmp_path / "odd.csv"
    p.write_text("Box;Brain;Sockets;Cores per CPU;Mem;Busy max\n"
                 "nodeA01;Intel Xeon Gold 6230;2;20;384;55\nnodeA02;Intel Xeon Gold 6230;2;20;384;60\n",
                 encoding="utf-8")
    return p


def test_llm_mock_redaction_and_merge(tmp_path: Path) -> None:
    prompts: list[str] = []
    calls = {"n": 0}

    def fake(prompt: str) -> object:
        prompts.append(prompt)
        calls["n"] += 1
        if calls["n"] == 1:
            return "not json"  # first answer invalid -> one retry
        tid = json.loads(prompt.split("Tables:\n", 1)[1])[0]["table_id"]
        return {"tables": [{"table_id": tid, "classification": "physical_hosts", "columns": [
            {"column": "Box", "target": "host_id", "confidence": 0.9},
            {"column": "Mem", "target": "ram_gb", "unit": "GiB", "confidence": 0.85},
            {"column": "Busy max", "target": "cpu_util_peak_pct", "unit": "pct", "confidence": 0.8}]}]}

    r = universal_ingest([_unknown_headers_file(tmp_path)], policy=load_policy(), llm_client=fake,
                         profiles_dir=tmp_path)[0]
    assert calls["n"] == 2
    assert "nodeA01" not in prompts[0] and "<NAME_" in prompts[0]  # hostnames redacted
    h = r.hosts[0]
    assert h.host_id == "nodeA01" and h.ram_gb == 384 and h.cpu_util_peak_pct == 55
    assert any(m.method == "llm" for m in r.mappings)


def test_llm_failure_falls_back(tmp_path: Path) -> None:
    def boom(prompt: str) -> object:
        raise ConnectionError("no network")

    r = universal_ingest([_unknown_headers_file(tmp_path)], policy=load_policy(), llm_client=boom,
                         profiles_dir=tmp_path)[0]
    assert any("rules-only" in t for t in r.transformations)


def test_redactor() -> None:
    red = Redactor(["Acme Corp"])
    out = red.redact("srv01.acme.local 10.1.2.3 Acme Corp Intel Xeon Gold 6230")
    assert "10.1.2.3" not in out and "acme.local" not in out and "Acme Corp" not in out
    assert "Xeon Gold 6230" in out
    assert red.unredact(out).startswith("srv01.acme.local")


def test_review_modes(tmp_path: Path) -> None:
    f = _unknown_headers_file(tmp_path)
    pol = load_policy()
    strict = universal_ingest([f], policy=pol, review_mode="strict", llm=False, profiles_dir=tmp_path / "a")[0]
    assert all(m.confidence >= 0.9 for m in strict.mappings if m.accepted)
    answers = iter(["n"] * 50)
    io = ReviewIO(console=Console(file=open(tmp_path / "out.txt", "w")), ask=lambda q: next(answers),
                  interactive=True)
    inter = universal_ingest([f], policy=pol, review_mode="interactive", llm=False, io=io,
                             profiles_dir=tmp_path / "b")[0]
    assert all(m.confidence >= 0.9 for m in inter.mappings if m.accepted)


def test_llm_response_model() -> None:
    LlmResponse.model_validate({"tables": [{"table_id": "x", "classification": "vms", "columns": []}]})
