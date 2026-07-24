"""KI-analyse av månedens økonomi (valgfritt).

Løfter den regelbaserte oppsummeringen (`aggregate._build_summary`) til en ekte
språkmodell som aktivt flagger AVVIK, mulige DATAFEIL (feilkategorisering,
manglende poster) og gir KONKRETE RÅD. Aktiveres kun når `ANTHROPIC_API_KEY`
er satt i `.env` – uten nøkkel brukes fortsatt den regelbaserte teksten, og
INGEN data forlater serveren.

PERSONVERN – kritisk: kun AGGREGERTE tall sendes til Anthropic (kategorisummer,
inntekt/forbruk/budsjett/sparerate/lånerenter). Aldri enkelttransaksjoner,
mottakernavn, personnavn, kontonummer eller rå bankdata. `_build_payload`
plukker eksplisitt ut kun trygge felt – kategorienes `items`-liste (som
inneholder enkeltkjøp) tas ALDRI med.
"""
from __future__ import annotations

import json

import httpx

from . import aggregate, config, db

_TIMEOUT = httpx.Timeout(60.0)
_ANTHROPIC_VERSION = "2023-06-01"


def api_key() -> str:
    """Effektiv API-nøkkel: DB-innstilling (satt i grensesnittet) har forrang,
    ellers .env. Da kan Frode bytte nøkkel fra Innstillinger uten å røre filer."""
    return (db.get_setting("anthropic_api_key", "") or "").strip() or config.ANTHROPIC_API_KEY


def model() -> str:
    return (db.get_setting("ai_model", "") or "").strip() or config.AI_MODEL


def configured() -> bool:
    return bool(api_key())


def status_dict() -> dict:
    """Trygg status til grensesnittet – aldri hele nøkkelen, kun et maskert hint."""
    k = api_key()
    from_db = bool((db.get_setting("anthropic_api_key", "") or "").strip())
    hint = (k[:8] + "…" + k[-4:]) if len(k) > 14 else ("…" if k else "")
    return {
        "configured": bool(k),
        "hint": hint,
        "source": "innstillinger" if from_db else ("env-fil" if config.ANTHROPIC_API_KEY else "ingen"),
        "model": model(),
    }


def clear_cache() -> None:
    _cache.clear()

# Enkel cache pr. (måned, personer) så vi ikke betaler for et nytt kall hver gang
# brukeren åpner analysen. Tømmes ved omstart – godt nok for hjemmebruk.
_cache: dict[tuple, dict] = {}

_SYSTEM = (
    "Du er en nøktern norsk privatøkonomi-rådgiver. Du får KUN aggregerte "
    "månedstall (kategorisummer, budsjett, inntekt/forbruk, sparerate, "
    "lånerenter) – ingen enkelttransaksjoner. Oppgaven din er å gi eieren "
    "(Frode) en kort, handlingsrettet analyse som:\n"
    "1) Peker på tydelige AVVIK/utliggere (f.eks. en kategori langt over "
    "budsjett eller langt over sitt eget 3-måneders snitt).\n"
    "2) Flagger MULIGE DATAFEIL: en forventet kategori som er 0 eller "
    "uventet lav, inntekt som mangler/virker for lav, eller tall som ikke "
    "henger sammen – som kan bety feilkategorisering eller manglende synk.\n"
    "3) Gir 1–3 KONKRETE råd der det er relevant (ikke generiske floskler).\n\n"
    "Regler: Svar på norsk. Vær kortfattet – maks ~5 korte kulepunkter, "
    "ingen innledning eller oppsummering utenom punktene. Ikke gjenta alle "
    "tallene; nevn bare det som er verdt å merke seg. Finn ALDRI på tall som "
    "ikke står i dataene. Vær konservativ med alarmer – flagg kun det du er "
    "rimelig sikker på, så brukeren slipper støy. Svar kun med selve analysen."
)


def _safe_categories(dash: dict) -> list[dict]:
    """Strip kategoriene til trygge, aggregerte felt – dropp `items` (enkeltkjøp)."""
    out = []
    for c in dash.get("categories", []):
        out.append({
            "navn": c.get("name"),
            "beløp": c.get("amount"),
            "budsjett": c.get("budget") or 0,
            "andelPct": c.get("pct"),
            "overBudsjett": c.get("over", False),
            "fast": c.get("fixed", False),
        })
    return out


def _build_payload(dash: dict) -> dict:
    """Bygg det AGGREGERTE datasettet som sendes til modellen. Kun trygge felt.

    Bank-helse tas med som anonym FLAGG/antall (ikke banknavn/tekst), slik at
    modellen kan varsle om mulig manglende synk uten at navn forlater huset."""
    k = dash.get("kpis", {})
    b = dash.get("budget", {})
    ls = dash.get("loanSplit", {})
    alerts = dash.get("alerts", []) or []
    return {
        "måned": dash.get("monthLabel"),
        "inntekt": k.get("income"),
        "forbruk": k.get("expense"),
        "overskudd": k.get("surplus"),
        "overskuddNegativt": k.get("surplusNeg", False),
        "spareratePct": k.get("savingsRate"),
        "sparemålPct": k.get("savingsGoal"),
        "fasteUtgifter": k.get("fixed"),
        "fastAndelPct": k.get("fixedPct"),
        "budsjett": {
            "totalt": b.get("total"),
            "brukt": b.get("spent"),
            "gjenstår": b.get("remaining"),
            "variabelt": b.get("variable"),
        },
        "lånerenterKr": ls.get("interest") if ls.get("hasData") else None,
        "låneavdragKr": ls.get("principal") if ls.get("hasData") else None,
        "kategorier": _safe_categories(dash),
        "antallBankvarsler": len(alerts),
    }


def _call(system: str, user: str) -> str:
    """Kall Messages API rått via httpx (samme mønster som bank-integrasjonene –
    ingen ny tung avhengighet). Kaster ved feil; håndteres av `_run`."""
    # Ikke send «thinking» – nyere modeller (Sonnet 5) avviser
    # {"type":"disabled"} med 400. Utelates → modellens standard, alltid gyldig.
    # Litt ekstra max_tokens så et evt. resonnement ikke spiser opp svaret.
    body = {
        "model": model(),
        "max_tokens": 1500,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    resp = httpx.post(
        f"{config.ANTHROPIC_BASE_URL}/v1/messages",
        headers={
            "x-api-key": api_key(),
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        json=body,
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    return "\n".join(p for p in parts if p).strip()


def _run(system: str, user: str) -> tuple[str | None, str | None]:
    """Kjør et kall og oversett feil til en lesbar norsk melding. Viser Anthropics
    FAKTISKE feiltekst (f.eks. «credit balance too low», ugyldig modell) i stedet
    for bare statuskoden. Returnerer (tekst, feil) – nøyaktig én er satt."""
    try:
        return _call(system, user), None
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        msg = ""
        try:
            msg = ((e.response.json() or {}).get("error", {}) or {}).get("message", "")
        except ValueError:
            msg = (e.response.text or "")[:200]
        if status == 401:
            detail = "ugyldig API-nøkkel (sjekk nøkkelen i Innstillinger)"
        elif status == 429:
            detail = "ratebegrenset – prøv igjen om litt"
        else:
            detail = f"HTTP {status}" + (f": {msg}" if msg else "")
        return None, f"KI-en feilet – {detail}"
    except (httpx.HTTPError, ValueError, KeyError) as e:
        return None, f"KI-en feilet: {e}"


def generate(month: str | None = None, persons: str | None = None,
             force: bool = False) -> dict:
    """Returner KI-analysen for gitt måned. Faller tilbake på {available: False}
    når ingen nøkkel er satt, og på {error: ...} ved API-feil – slik at
    frontend alltid kan vise den regelbaserte oppsummeringen ved siden av."""
    if not configured():
        return {"available": False}

    dash = aggregate.build_dashboard(month, persons)
    key = (dash.get("month"), persons or "")
    if not force and key in _cache:
        return {**_cache[key], "cached": True}

    user = ("Her er månedens aggregerte tall (JSON). Gi analysen:\n\n"
            + json.dumps(_build_payload(dash), ensure_ascii=False, indent=2))
    text, err = _run(_SYSTEM, user)
    if err:
        return {"available": True, "text": None, "error": err.replace("KI-en", "KI-analysen")}

    result = {"available": True, "text": text or None, "model": model()}
    if text:
        _cache[key] = result
    return {**result, "cached": False}


_QA_SYSTEM = (
    "Du er en hjelpsom norsk privatøkonomi-assistent for eieren (Frode). Du får "
    "KUN aggregerte tall (kategorisummer, budsjett, inntekt/forbruk, sparerate, "
    "lånerenter, netto formue, cashflow pr. måned) – ALDRI enkelttransaksjoner, "
    "mottakere, datoer eller navn. Svar kort og konkret på spørsmålet basert på "
    "tallene du har. Regn gjerne enkelt der det hjelper. Hvis spørsmålet krever "
    "data du IKKE har (spesifikke kjøp, hvem/hvor, en konkret dato), si tydelig "
    "at du ikke har den detaljen her, og at det må sjekkes i transaksjonslista. "
    "Finn ALDRI på tall. Svar på norsk, uten unødig innledning."
)


def _qa_payload(dash: dict) -> dict:
    """Kontekst for spørsmål-svar: samme trygge, aggregerte grunnlag som analysen,
    pluss netto formue og cashflow-historikk (label + netto) for litt trend."""
    p = _build_payload(dash)
    p["nettoFormue"] = dash.get("kpis", {}).get("netWorth")
    p["cashflowSisteMnd"] = [
        {"mnd": c.get("month"), "nettoKr": c.get("net")} for c in dash.get("cashflow", [])
    ]
    return p


def ask(question: str, month: str | None = None, persons: str | None = None) -> dict:
    """Fritekst-spørsmål om økonomien. Kun aggregerte tall sendes som kontekst."""
    if not configured():
        return {"available": False}
    q = (question or "").strip()
    if not q:
        return {"available": True, "answer": None, "error": "Skriv inn et spørsmål."}
    q = q[:500]  # kort spørsmål; hindrer at store mengder tekst sendes ut

    dash = aggregate.build_dashboard(month, persons)
    user = ("Aggregerte tall (JSON):\n\n"
            + json.dumps(_qa_payload(dash), ensure_ascii=False, indent=2)
            + "\n\nSpørsmål: " + q)
    text, err = _run(_QA_SYSTEM, user)
    if err:
        return {"available": True, "answer": None, "error": err}
    return {"available": True, "answer": text or None, "model": model()}
