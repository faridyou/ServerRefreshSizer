"""CPU display everywhere: 'Name (cores, GHz)' plus a hover tooltip (cores/threads, base/turbo GHz, L3 cache, TDP).

One place so the app, the HTML/PDF/Word/PowerPoint/Excel reports and the email prompt all show CPUs the same way.
Lookup: catalog id, exact catalog name, then the CPU matcher (pattern/exact or fuzzy >= 0.9). A CPU that is not in
the catalog keeps the cores/GHz known from the input data, and its tooltip says it is not in the catalog.
"""

from __future__ import annotations

from functools import lru_cache
from html import escape
from typing import Any

from markupsafe import Markup

from refreshsizer.catalog.loader import load_cpus
from refreshsizer.model.entities import CpuModel

WORDS = {
    "en": {"cores": "cores", "threads": "threads", "base": "base", "turbo": "turbo", "cache": "L3 cache",
           "tdp": "TDP", "unknown": "not in the CPU catalog: values from the input data", "per_cpu": "per CPU"},
    "fr": {"cores": "cœurs", "threads": "threads", "base": "base", "turbo": "turbo", "cache": "cache L3",
           "tdp": "TDP", "unknown": "absent du catalogue : valeurs issues des données d'entrée", "per_cpu": "par CPU"},
}


@lru_cache(maxsize=4096)
def find_cpu(key: str | None) -> CpuModel | None:
    if not key or not str(key).strip():
        return None
    cpus = load_cpus()
    if key in cpus:
        return cpus[key]
    low = str(key).strip().lower()
    for c in cpus.values():
        if c.model.lower() == low:
            return c
    from refreshsizer.benchmarks.cpu_matcher import match_cpu

    m = match_cpu(key)
    if m and m.confidence >= 0.9 and m.cpu_id in cpus:
        return cpus[m.cpu_id]
    return None


def _num(v: float | None) -> str:
    return f"{v:g}" if v is not None else ""


def _ghz(v: float | None) -> str:
    """3 -> '3.0', 2.75 -> '2.75'."""
    if v is None:
        return ""
    return f"{v:.1f}" if round(v * 10) == v * 10 else f"{v:.2f}"


def _w(lang: str) -> dict[str, str]:
    return WORDS.get(lang, WORDS["en"])


def cpu_specs(key: str | None, cores: float | None = None, ghz: float | None = None, lang: str = "en"
              ) -> tuple[str, str]:
    """(display name, '(cores, GHz)') - catalog values first, else the cores/GHz given by the input data."""
    c = find_cpu(key)
    w = _w(lang)
    name = c.model if c else (str(key).strip() if key else "")
    n_cores = c.cores if c else (int(cores) if cores else None)
    g = (c.base_ghz or None) if c else ghz
    parts = [f"{n_cores} {w['cores']}" if n_cores else "", f"{_ghz(g)} GHz" if g else ""]
    parts = [p for p in parts if p]
    return name, f"({', '.join(parts)})" if parts else ""


def cpu_label(key: str | None, cores: float | None = None, ghz: float | None = None, lang: str = "en") -> str:
    name, spec = cpu_specs(key, cores, ghz, lang)
    return f"{name} {spec}".strip()


def cpu_tip(key: str | None, cores: float | None = None, ghz: float | None = None, lang: str = "en") -> str:
    """Multi-line tooltip: name, cores/threads, base/turbo GHz, L3 cache, TDP (codename, family)."""
    c = find_cpu(key)
    w = _w(lang)
    if c is None:
        lines = [str(key or "").strip()]
        if cores:
            lines.append(f"{int(cores)} {w['cores']}")
        if ghz:
            lines.append(f"{w['base']} {_ghz(ghz)} GHz")
        lines.append(w["unknown"])
        return "\n".join(x for x in lines if x)
    lines = [c.model + (f" ({c.codename})" if c.codename and c.codename.lower() not in c.model.lower() else ""),
             f"{c.cores} {w['cores']} / {c.cores * (c.threads_per_core or 1)} {w['threads']}"]
    ghz_line = f"{w['base']} {_ghz(c.base_ghz)} GHz" if c.base_ghz else ""
    if c.turbo_ghz:
        ghz_line += (", " if ghz_line else "") + f"{w['turbo']} {_ghz(c.turbo_ghz)} GHz"
    if ghz_line:
        lines.append(ghz_line)
    if c.l3_mb:
        lines.append(f"{w['cache']} {_num(c.l3_mb)} MB")
    if c.tdp_w:
        lines.append(f"{w['tdp']} {_num(c.tdp_w)} W")
    return "\n".join(lines)


def cpu_html(key: str | None, cores: float | None = None, ghz: float | None = None, lang: str = "en",
             prefix: str = "") -> Markup:
    """<span class="cpu" title=... data-tip=...>[prefix]Name (cores, GHz)</span>.

    The native `title` tooltip works in every browser without JavaScript; when the page script runs it removes the
    title on hover and shows the styled popup (data-tip) instead, so only one tooltip appears."""
    if not key:
        return Markup("")
    tip = escape(cpu_tip(key, cores, ghz, lang)).replace("\n", "&#10;")
    text = escape(prefix + cpu_label(key, cores, ghz, lang))
    return Markup(f'<span class="cpu" tabindex="0" title="{tip}" data-tip="{tip}">{text}</span>')


def config_label(cfg: Any, lang: str = "en") -> str:
    """'R6715 1x AMD EPYC 9335 (32 cores, 2.75 GHz)' for a TargetConfig (or a Candidate)."""
    cfg = getattr(cfg, "config", cfg)
    return f"{cfg.platform} {cfg.cpu_count}x {cpu_label(cfg.cpu_id, lang=lang)}"


def config_html(cfg: Any, lang: str = "en") -> Markup:
    cfg = getattr(cfg, "config", cfg)
    return Markup(f"{escape(cfg.platform)} ") + cpu_html(cfg.cpu_id, lang=lang, prefix=f"{cfg.cpu_count}x ")


def register(env: Any) -> None:
    """Jinja filters (language taken from the template variable `lang`, default English):
    {{ name | cpu }} / {{ name | cpu(cores, ghz) }} -> hover span; {{ x | cfg }} -> 'R6715 1x CPU (...)' span;
    {{ name | cpu_text }}, {{ x | cfg_text }} -> plain text."""
    from jinja2 import pass_context

    def lang(ctx: Any) -> str:
        v = ctx.get("lang")
        return str(v) if v in WORDS else "en"

    env.filters["cpu"] = pass_context(lambda ctx, k, cores=None, ghz=None: cpu_html(k, cores, ghz, lang(ctx)))
    env.filters["cpu_text"] = pass_context(lambda ctx, k, cores=None, ghz=None: cpu_label(k, cores, ghz, lang(ctx)))
    env.filters["cfg"] = pass_context(lambda ctx, x: config_html(x, lang(ctx)))
    env.filters["cfg_text"] = pass_context(lambda ctx, x: config_label(x, lang(ctx)))


CPU_CSS = (".cpu{border-bottom:1px dotted #777;cursor:help}"
           "#cpu-tip{position:fixed;z-index:1000;display:none;max-width:340px;padding:6px 9px;white-space:pre-line;"
           "font-size:12px;font-weight:400;line-height:1.4;color:#fff;background:#1F2A37;border-radius:6px;"
           "pointer-events:none;box-shadow:0 4px 14px rgba(0,0,0,.25)}"
           "@media print{.cpu{border-bottom:0}#cpu-tip{display:none!important}}")

# One floating popup (position: fixed) so tables inside scrolling cards cannot clip it. Plain ES5, no dependency;
# the native title tooltip is the fallback when scripts are blocked. Used inline by the HTML report and as
# static/cpu_tip.js by the app.
CPU_JS = """(function () {
  var tip = null;
  function cpuOf(t) { while (t && t.nodeType !== 1) { t = t.parentNode; }
    return (t && t.closest) ? t.closest(".cpu[data-tip]") : null; }
  function show(el) {
    if (el.hasAttribute("title")) { el.removeAttribute("title"); }
    if (!tip) { tip = document.createElement("div"); tip.id = "cpu-tip"; document.body.appendChild(tip); }
    tip.textContent = el.getAttribute("data-tip");
    tip.style.display = "block";
    var r = el.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    var x = Math.min(r.left, window.innerWidth - w - 8), y = r.bottom + 6;
    if (y + h > window.innerHeight - 4) { y = r.top - h - 6; }
    tip.style.left = Math.max(4, x) + "px"; tip.style.top = Math.max(4, y) + "px";
  }
  function hide() { if (tip) { tip.style.display = "none"; } }
  document.addEventListener("mouseover", function (e) { var el = cpuOf(e.target); if (el) { show(el); } else { hide(); } });
  document.addEventListener("focusin", function (e) { var el = cpuOf(e.target); if (el) { show(el); } });
  document.addEventListener("focusout", hide);
  document.addEventListener("mouseleave", hide);
  window.addEventListener("scroll", hide, true);
})();"""
