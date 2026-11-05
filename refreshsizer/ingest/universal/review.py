"""Mapping review (runtime): interactive in a terminal, --yes (auto-accept >= 0.6), --strict (refuse < 0.9)."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from rich.console import Console
from rich.table import Table

from refreshsizer.config.schema import Policy
from refreshsizer.ingest.universal.mapping_rules import ALL_FIELDS, ColumnMapping


@dataclass
class ReviewIO:
    console: Console = field(default_factory=lambda: Console(stderr=True))
    ask: Callable[[str], str] = input
    interactive: bool = field(default_factory=lambda: sys.stdin.isatty())


def show(table_label: str, maps: list[ColumnMapping], samples: dict[str, tuple[str, str]], io: ReviewIO) -> None:
    t = Table(title=f"Mapping review - {table_label}", show_lines=False)
    for h in ("source column", "-> canonical field", "unit", "conf.", "method", "sample", "transformed"):
        t.add_column(h)
    for m in maps:
        s, tr = samples.get(m.column, ("", ""))
        style = "green" if m.confidence >= 0.9 else "yellow" if m.confidence >= 0.6 else "red"
        t.add_row(m.column, m.target or "-", m.unit or "", f"[{style}]{m.confidence:.2f}[/{style}]", m.method,
                  s[:30], tr[:30])
    io.console.print(t)


def review(table_label: str, maps: list[ColumnMapping], samples: dict[str, tuple[str, str]], mode: str,
           policy: Policy, io: ReviewIO | None = None) -> tuple[list[ColumnMapping], list[str]]:
    """Return (accepted mappings, assumption notes). Rejected mappings get target=None."""
    io = io or ReviewIO()
    notes: list[str] = []
    if mode == "interactive" and not io.interactive:
        mode = "yes"
        notes.append("non-interactive session: --yes behaviour applied to mapping review")
    if mode == "interactive":
        show(table_label, maps, samples, io)
    thr = policy.mapping_strict_threshold if mode == "strict" else policy.mapping_yes_threshold
    out = []
    for m in maps:
        if m.target is None:
            out.append(m)
            continue
        if mode == "interactive" and m.confidence < policy.mapping_autoaccept:
            ans = io.ask(f"{table_label}: '{m.column}' -> {m.target} ({m.confidence:.2f}). "
                         f"Accept [Y], reject [n] or type a field name: ").strip()
            if ans.lower() in ("n", "no", "non"):
                notes.append(f"user rejected '{m.column}' -> {m.target}")
                m = ColumnMapping(m.column, None, 0.0, "user", m.unit)
            elif ans and ans not in ("y", "yes", "o", "oui") and ans in ALL_FIELDS:
                m = ColumnMapping(m.column, ans, 1.0, "user", m.unit)
            elif m.confidence >= 0:
                m = ColumnMapping(m.column, m.target, max(m.confidence, policy.mapping_autoaccept), "user", m.unit)
            out.append(m)
            continue
        if m.confidence >= thr or m.method in ("profile", "user"):
            out.append(m)
        else:
            notes.append(f"ASSUMPTION: '{m.column}' -> {m.target} not used (confidence {m.confidence:.2f} < "
                         f"{thr} in {mode} mode)")
            out.append(ColumnMapping(m.column, None, m.confidence, m.method, m.unit))
    return out, notes
