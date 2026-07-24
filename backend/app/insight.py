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

import httpx

from . import aggregate, config

_TIMEOUT = httpx.Timeout(60.0)
_ANTHROPIC_VERSION = "2023-06-01"

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


def _call_anthropic(payload: dict) -> str:
    """Kall Messages API rått via httpx (samme mønster som bank-integrasjonene –
    ingen ny tung avhengighet). Kaster ved feil; håndteres av `generate`."""
    import json

    body = {
        "model": config.AI_MODEL,
        "max_tokens": 1024,
        # Analysen er liten; slå av «thinking» for å holde token/kostnad nede.
        "thinking": {"type": "disabled"},
        "system": _SYSTEM,
        "messages": [{
            "role": "user",
            "content": (
                "Her er månedens aggregerte tall (JSON). Gi analysen:\n\n"
                + json.dumps(payload, ensure_ascii=False, indent=2)
            ),
        }],
    }
    resp = httpx.post(
        f"{config.ANTHROPIC_BASE_URL}/v1/messages",
        headers={
            "x-api-key": config.ANTHROPIC_API_KEY,
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


def generate(month: str | None = None, persons: str | None = None,
             force: bool = False) -> dict:
    """Returner KI-analysen for gitt måned. Faller tilbake på {available: False}
    når ingen nøkkel er satt, og på {error: ...} ved API-feil – slik at
    frontend alltid kan vise den regelbaserte oppsummeringen ved siden av."""
    if not config.ai_configured():
        return {"available": False}

    dash = aggregate.build_dashboard(month, persons)
    key = (dash.get("month"), persons or "")
    if not force and key in _cache:
        return {**_cache[key], "cached": True}

    payload = _build_payload(dash)
    try:
        text = _call_anthropic(payload)
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        detail = "ratebegrenset – prøv igjen om litt" if status == 429 else f"HTTP {status}"
        return {"available": True, "text": None, "error": f"KI-analysen feilet ({detail})."}
    except (httpx.HTTPError, ValueError, KeyError) as e:
        return {"available": True, "text": None, "error": f"KI-analysen feilet: {e}"}

    result = {"available": True, "text": text or None, "model": config.AI_MODEL}
    if text:
        _cache[key] = result
    return {**result, "cached": False}
