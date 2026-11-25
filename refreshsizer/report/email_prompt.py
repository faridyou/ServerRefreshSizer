"""Customer email draft as an AI prompt.

RefreshSizer does not write the email itself (it must work offline and without an AI). Instead it builds a
self-contained prompt - instructions plus every relevant sizing fact - that the presales engineer pastes into any
AI assistant. The facts come from the same report context as the customer report, so the email cannot drift from
the report. Internal prices are left out unless explicitly requested.
"""

from __future__ import annotations

from typing import Any

from refreshsizer.catalog.cpu_info import cpu_label
from refreshsizer.model.entities import Project
from refreshsizer.report.explain import money

AUDIENCES = ("executive", "technical")
TONES = ("professional", "friendly", "formal")

TXT: dict[str, dict[str, Any]] = {
    "en": {
        "role": "You are a Dell Technologies presales engineer. Write an email to a customer about the server "
                "refresh sizing below.",
        "aud_executive": "Audience: IT management and decision makers. Lead with the outcome (how many servers, "
                         "why fewer than today, what it means for licences, space and power), keep technical "
                         "detail to a minimum.",
        "aud_technical": "Audience: the customer's infrastructure team. Include the key figures (cores, "
                         "per-core performance, utilization, memory, storage capacity and layout, connectivity).",
        "tone": "Tone: {tone}, clear and confident, no marketing superlatives.",
        "rules_head": "Rules:",
        "rules": [
            "Use ONLY the facts listed below. Do not invent numbers, prices, product features, dates or "
                "commitments. If something is not in the facts, leave it out or ask for it.",
            "Keep the numbers exactly as given (you may round for readability, never change them).",
            "Explain the benchmark simply: the comparison uses the published SPEC CPU 2017 (SPECrate2017_int_base) "
                "results, i.e. measured performance per core, not core counts or clock speed (GHz).",
            "Present the sizing as a preliminary proposal to be validated (pilot, final configuration and "
                "quotation with Dell).",
            "Structure: subject line; short greeting; 2-3 sentence summary; the proposal as a few bullet points; "
                "storage in one short paragraph; next steps; the information still needed from the customer as a "
                "numbered list; polite close with the sender's name.",
            "Length: {length} words for the body.",
            "Write the email in English.",
        ],
        "no_prices": "Do not mention any price, cost or licence amount.",
        "prices": "You may mention the indicative cost figures below, always as budgetary estimates based on "
                  "list-price placeholders, never as a quote.",
        "low_conf": "The input data has low confidence for some clusters (see the facts): say clearly that the "
                    "sizing must be confirmed with a performance collection before any order.",
        "recipient": "Recipient", "sender": "Sender", "company": "Customer company",
        "facts": "FACTS", "notes": "Additional context from the sender (use it if relevant)",
        "current": "Current environment", "bench": "Benchmark (per-core performance)", "proposal": "Proposal",
        "naive": "Like-for-like comparison", "storage": "Storage", "alts": "Alternative processor options",
        "costs": "Indicative costs (placeholders, not a quote)", "next": "Next steps",
        "questions": "Information still needed from the customer", "end": "Return only the email (subject + body).",
    },
    "fr": {
        "role": "Vous êtes ingénieur avant-vente Dell Technologies. Rédigez un e-mail à un client au sujet du "
                "dimensionnement de renouvellement de serveurs ci-dessous.",
        "aud_executive": "Public : direction informatique et décideurs. Commencez par le résultat (combien de "
                         "serveurs, pourquoi moins qu'aujourd'hui, impact sur les licences, l'espace et "
                         "l'énergie), limitez les détails techniques.",
        "aud_technical": "Public : l'équipe infrastructure du client. Incluez les chiffres clés (cœurs, "
                         "performance par cœur, utilisation, mémoire, capacité et configuration du stockage, "
                         "connectivité).",
        "tone": "Ton : {tone}, clair et assuré, sans superlatifs marketing.",
        "rules_head": "Règles :",
        "rules": [
            "Utilisez UNIQUEMENT les faits listés ci-dessous. N'inventez ni chiffres, ni prix, ni fonctionnalités, "
                "ni dates, ni engagements. Si une information manque, omettez-la ou demandez-la.",
            "Gardez les chiffres tels quels (arrondis possibles pour la lisibilité, jamais modifiés).",
            "Expliquez simplement le benchmark : la comparaison s'appuie sur les résultats publiés SPEC CPU 2017 "
                "(SPECrate2017_int_base), c'est-à-dire la performance mesurée par cœur, et non le nombre de cœurs "
                "ou la fréquence (GHz).",
            "Présentez le dimensionnement comme une proposition préliminaire à valider (pilote, configuration "
                "finale et devis avec Dell).",
            "Structure : objet ; salutation courte ; résumé en 2-3 phrases ; la proposition en quelques puces ; le "
                "stockage en un court paragraphe ; prochaines étapes ; les informations encore attendues du client "
                "en liste numérotée ; formule de politesse et nom de l'expéditeur.",
            "Longueur : {length} mots pour le corps du message.",
            "Rédigez l'e-mail en français.",
        ],
        "no_prices": "Ne mentionnez aucun prix, coût ni montant de licence.",
        "prices": "Vous pouvez mentionner les coûts indicatifs ci-dessous, toujours comme estimations budgétaires "
                  "basées sur des prix catalogue indicatifs, jamais comme un devis.",
        "low_conf": "Les données d'entrée sont peu fiables pour certains clusters (voir les faits) : indiquez "
                    "clairement que le dimensionnement doit être confirmé par une collecte de performances avant "
                    "toute commande.",
        "recipient": "Destinataire", "sender": "Expéditeur", "company": "Société cliente",
        "facts": "FAITS", "notes": "Contexte complémentaire de l'expéditeur (à utiliser si pertinent)",
        "current": "Environnement actuel", "bench": "Benchmark (performance par cœur)", "proposal": "Proposition",
        "naive": "Comparaison à l'identique", "storage": "Stockage", "alts": "Autres options de processeur",
        "costs": "Coûts indicatifs (valeurs indicatives, pas un devis)", "next": "Prochaines étapes",
        "questions": "Informations encore attendues du client", "end": "Renvoyez uniquement l'e-mail (objet + corps).",
    },
}
TONE_FR = {"professional": "professionnel", "friendly": "cordial", "formal": "formel"}


def _cluster_facts(c: dict[str, Any], t: Any, x: dict[str, str], prices: bool) -> list[str]:
    d, r, s, cs = c["d"], c["rec"], c["src"], c["cs"]
    out = [f"## {d.cluster}"]
    lang = getattr(t, "lang", "en")
    cpus = ", ".join(f"{m['hosts']} servers with {cpu_label(m['cpu'], m.get('cpu_cores'), m['ghz'], lang)}, "
                     f"{m['cores']} {t('cores').lower()} in total" for m in s["models"]) or "-"
    cur = (f"- {x['current']}: {s['hosts']} {t('c_servers')}, {s['cores']} {t('cores').lower()} "
           f"({s['ghz_total']:,.0f} GHz), {s['ram_gb']:,} GB RAM, {s['vms']} VMs / {s['vcpu']:,.0f} vCPU, "
           f"{s['storage_used_tib']:,.1f} TiB; CPUs: {cpus}")
    if s["util_pct"] is not None:
        cur += f"; {t('c_cpu_util')}: {s['util_pct']} % ({s['util_basis']})"
    if s.get("collection_days"):
        cur += f"; collection: {s['collection_days']:g} days"
    out.append(cur)
    out.append(f"- Data confidence: {d.mode.value} / {d.confidence.value}"
               + (f" - {t(c['banner'])}" if c["banner"] else ""))
    src_b = next((b for b in c["benchmarks"] if b["side"] == "source" and b["metric"] != "rPerf"), None)
    tgt_b = next((b for b in c["benchmarks"] if b["side"] == "target"), None)
    if src_b and tgt_b:
        ratio = f" -> x{s['perf_ratio']} per core" if s.get("perf_ratio") else ""
        out.append(f"- {x['bench']}: {cpu_label(src_b['cpu'], lang=lang)} {src_b['spc']} per core "
                   f"({src_b['system']}) vs {cpu_label(tgt_b['cpu'], lang=lang)} {tgt_b['spc']} per core "
                   f"({tgt_b['system']}){ratio}")
    if not r:
        out.append(f"- {x['proposal']}: no feasible configuration (review constraints)")
        return out
    mem = f"{r.dimm.total_gb} GB" if r.dimm else "-"
    out.append(f"- {x['proposal']}: {r.n_total} x Dell PowerEdge {r.config.platform}, {r.config.cpu_count} x "
               f"{cpu_label(r.config.cpu_id, lang=lang)} and {mem} per server, network "
               f"{r.nics}; {r.n_total * r.config.total_cores} {t('cores').lower()} in total; "
               f"{r.util_failed:.0%} CPU load with one server down; sized for {d.growth_cpu_pct:g} %/yr CPU growth "
               f"over {d.years} years, max {d.u_max:.0%} CPU, {r.k_ha} spare server(s) (N+{r.k_ha})")
    if cs.naive:
        out.append(f"- {x['naive']}: same core count would need {cs.naive.core_match_hosts} servers, same GHz "
                   f"{cs.naive.ghz_match_hosts} servers (ignores the per-core gain)")
    st = cs.storage
    if st:
        line = f"- {x['storage']}: {st.solution}; {st.used0_tib:,.1f} TiB used today -> {st.used_t_tib:,.1f} TiB " \
               f"in year {st.years} ({st.growth_pct:g} %/yr), data reduction {st.drr:.1f}:1"
        if st.usable_tib:
            line += f"; {st.usable_tib:,.1f} TiB usable / {st.effective_tib:,.1f} TiB effective, fill " \
                    f"{st.fill_today:.0%} today and {st.fill_t:.0%} in year {st.years}"
        if st.array:
            a = st.array
            line += f"; {a.appliances * a.drives_per_appliance} x {a.drive_tb:g} TB NVMe {a.media}, {a.rack_units} U"
        if st.architecture == "external":
            line += f"; {st.protocol_label}, {st.host_adapter} per server, 2 redundant fabrics; array layout to " \
                    "be validated with Dell's PowerStore sizing tools"
        out.append(line)
    alts = [y for y in cs.scenarios[:4] if y.label != r.label][:3]
    if alts:
        out.append(f"- {x['alts']}: " + "; ".join(
            f"{y.n_total} x PowerEdge {y.config.platform} {y.config.cpu_count} x {cpu_label(y.config.cpu_id, lang=lang)} "
            f"({y.util_failed:.0%} with one server down)" for y in alts))
    if prices:
        cost = [f"compute TCO over {d.years} yr {money(r.tco)} (hardware {money(r.capex_total)}, licences "
                f"{money(r.license_total)})"]
        if c.get("license_delta") is not None:
            cost.append(f"licensing delta vs today {money(c['license_delta'])}")
        if st and st.capex_usd:
            cost.append(f"storage {money(st.capex_usd)}")
        out.append(f"- {x['costs']}: " + "; ".join(cost))
    return out


def build_email_prompt(project: Project, lang: str = "en", audience: str = "executive", tone: str = "professional",
                       include_prices: bool = False, recipient: str = "", sender: str = "", company: str = "",
                       notes: str = "", ctx: dict[str, Any] | None = None) -> str:
    from refreshsizer.report.build import build_context

    lang = lang if lang in TXT else "en"
    audience = audience if audience in AUDIENCES else "executive"
    tone = tone if tone in TONES else "professional"
    x = TXT[lang]
    ctx = ctx or build_context(project, lang, "presales" if include_prices else "customer")
    t = ctx["t"]
    length = "250-400" if audience == "executive" else "350-550"
    lines = [x["role"], "", x[f"aud_{audience}"],
             x["tone"].replace("{tone}", TONE_FR[tone] if lang == "fr" else tone), "", x["rules_head"]]
    lines += [f"{i}. {r.replace('{length}', length)}" for i, r in enumerate(x["rules"], 1)]
    n = len(x["rules"])
    lines.append(f"{n + 1}. {x['prices'] if include_prices else x['no_prices']}")
    if any(c["banner"] for c in ctx["clusters"]):
        lines.append(f"{n + 2}. {x['low_conf']}")
    lines.append("")
    for k, v in (("recipient", recipient), ("company", company), ("sender", sender)):
        if v.strip():
            lines.append(f"{x[k]}: {v.strip()}")
    lines += ["", f"=== {x['facts']} ===", f"Project: {project.name} (sizing of {ctx['run'].generated_at[:10]})"]
    for c in ctx["clusters"]:
        lines += _cluster_facts(c, t, x, include_prices)
    steps = ([t("c_next_measure")] if any(c["banner"] for c in ctx["clusters"]) else []) + \
        [t("c_next_pilot"), t("c_next_info"), t("c_next_quote")]
    lines += ["", f"{x['next']}:"] + [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    if ctx["questions"]:
        lines += ["", f"{x['questions']}:"] + [f"{i}. {q}" for i, q in enumerate(ctx["questions"], 1)]
    if notes.strip():
        lines += ["", f"{x['notes']}:", notes.strip()]
    lines += ["", x["end"]]
    return "\n".join(lines) + "\n"
