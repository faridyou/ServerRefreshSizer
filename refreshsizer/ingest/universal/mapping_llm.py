"""Optional LLM step (Anthropic API) for sheet classification and unresolved/low-confidence columns.

The LLM only interprets structure and meaning; it never computes sizing values. Only headers, inferred
types/units and <= 10 REDACTED sample rows are sent. Any failure -> rules-only mode.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from refreshsizer.ingest.universal.classify import CLASSES
from refreshsizer.ingest.universal.mapping_rules import ALL_FIELDS, IP_RE
from refreshsizer.ingest.universal.profile import TableProfile

log = logging.getLogger("refreshsizer.llm")

FQDN_RE = re.compile(r"\b[a-z0-9-]+(\.[a-z0-9-]+){2,}\b", re.I)
IP_ANY_RE = re.compile(r"\b\d{1,3}(\.\d{1,3}){3}\b")
SAFE_RE = re.compile(r"xeon|epyc|power|sparc|gold|silver|platinum|proliant|poweredge|windows|linux|rhel|red hat|"
                     r"centos|ubuntu|suse|aix|solaris|esxi|cluster|gen\d|\bv\d\b|ghz|go\b|gb\b|%", re.I)
ID_LIKE_RE = re.compile(r"^[A-Za-z][\w.-]*\d[\w.-]*$")


class LlmColumn(BaseModel):
    column: str
    target: str | None = None
    unit: str | None = None
    confidence: float = Field(ge=0, le=1)
    extraction: str | None = None


class LlmTable(BaseModel):
    table_id: str
    classification: str
    columns: list[LlmColumn] = Field(default_factory=list)


class LlmResponse(BaseModel):
    tables: list[LlmTable] = Field(default_factory=list)


class Redactor:
    """Reversible redaction of hostnames, IPs, FQDNs and customer names (token map kept locally)."""

    def __init__(self, customer_names: list[str] | None = None):
        self.map: dict[str, str] = {}
        self.customers = [c for c in (customer_names or []) if c]
        self.n = 0

    def _tok(self, kind: str, value: str) -> str:
        if value not in self.map:
            self.n += 1
            self.map[value] = f"<{kind}_{self.n}>"
        return self.map[value]

    def redact(self, value: Any, column_is_identifier: bool = False) -> str:
        s = "" if value is None else str(value)
        for c in self.customers:
            s = re.sub(re.escape(c), lambda m: self._tok("CUSTOMER", m.group(0)), s, flags=re.I)
        s = IP_ANY_RE.sub(lambda m: self._tok("IP", m.group(0)), s)
        s = FQDN_RE.sub(lambda m: self._tok("FQDN", m.group(0)), s)
        if column_is_identifier and s and not SAFE_RE.search(s):
            return self._tok("NAME", s)
        if ID_LIKE_RE.match(s) and not SAFE_RE.search(s):
            return self._tok("NAME", s)
        return s

    def unredact(self, s: str) -> str:
        for k, v in self.map.items():
            s = s.replace(v, k)
        return s


def llm_enabled(setting: str | bool) -> bool:
    if setting in (False, "false", "off", "no"):
        return False
    if os.environ.get("REFRESHSIZER_OFFLINE", "").strip() not in ("", "0", "false"):
        return False
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


SYSTEM = (
    "You map customer infrastructure inventory spreadsheets to a canonical model. You only interpret structure "
    "and meaning (sheet classification, which column is which field, units, extraction rules such as splitting "
    "'2x Gold 6230' into sockets=2 and cpu_model='Intel Xeon Gold 6230'). Never compute sizing values. "
    "Values like <NAME_3> are redacted identifiers. Use target=null when no field fits."
)


def build_prompt(tables: list[TableProfile], red: Redactor, id_cols: dict[str, set[str]]) -> str:
    payload = []
    for t in tables:
        rows = []
        for r in t.rows[:10]:
            rows.append({k: red.redact(v, k in id_cols.get(t.table_id, set())) for k, v in r.items()})
        payload.append({
            "table_id": red.redact(t.table_id), "sheet": red.redact(t.sheet),
            "columns": [{"name": c.name, "dtype": c.dtype, "unit_guess": c.unit} for c in t.columns],
            "sample_rows": rows})
    return (f"Allowed classifications: {CLASSES}\nAllowed target fields: {ALL_FIELDS}\n"
            f"Units: GiB, MiB, TiB, KiB, GHz, MHz, pct, fraction.\nTables:\n{json.dumps(payload, ensure_ascii=False)}")


def _anthropic_call(model: str) -> Callable[[str], LlmResponse | None]:
    import anthropic

    client = anthropic.Anthropic(timeout=20.0, max_retries=2)

    def call(prompt: str) -> LlmResponse | None:
        resp = client.messages.parse(
            model=model, max_tokens=16000, system=SYSTEM,
            messages=[{"role": "user", "content": prompt}], output_format=LlmResponse)
        if resp.stop_reason == "refusal":
            log.warning("LLM declined the mapping request; rules-only mode")
            return None
        parsed = resp.parsed_output
        return parsed if isinstance(parsed, LlmResponse) else None

    return call


def run_llm(tables: list[TableProfile], model: str, id_cols: dict[str, set[str]],
            client: Callable[[str], Any] | None = None, customer_names: list[str] | None = None
            ) -> tuple[LlmResponse | None, str, Redactor]:
    """client: callable(prompt) -> LlmResponse | dict | JSON str (mockable). Returns (response, status, redactor)."""
    red = Redactor(customer_names)
    prompt = build_prompt(tables, red, id_cols)
    try:
        call = client or _anthropic_call(model)
    except Exception as e:  # SDK missing / misconfigured
        return None, f"LLM unavailable ({type(e).__name__}); rules-only mode", red
    for attempt in range(2):  # 1 retry on invalid output
        try:
            raw = call(prompt)
            if raw is None:
                return None, "LLM returned no usable output; rules-only mode", red
            if isinstance(raw, LlmResponse):
                res = raw
            elif isinstance(raw, dict):
                res = LlmResponse.model_validate(raw)
            else:
                m = re.search(r"\{.*\}", str(raw), re.S)
                res = LlmResponse.model_validate(json.loads(m.group(0) if m else str(raw)))
            for t in res.tables:
                t.table_id = red.unredact(t.table_id)
                if t.classification not in CLASSES:
                    t.classification = "irrelevant"
                for c in t.columns:
                    if c.target not in ALL_FIELDS:
                        c.target = None
            return res, "LLM mapping used (redacted headers/samples only)", red
        except (ValidationError, json.JSONDecodeError, ValueError) as e:
            log.info("LLM output invalid (attempt %d): %s", attempt + 1, e)
            continue
        except Exception as e:  # network, auth, rate limit ... never fail because of the LLM
            return None, f"LLM call failed ({type(e).__name__}); rules-only mode", red
    return None, "LLM output invalid twice; rules-only mode", red
