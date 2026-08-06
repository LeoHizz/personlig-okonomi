"""Konfigurasjon lest fra miljøvariabler (.env)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Last inn .env fra prosjektroten dersom den finnes (nyttig ved lokal kjøring).
_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on", "ja"}


# --- Bankdata-leverandør ---
# "enablebanking" (anbefalt – gratis personlig bruk) eller "gocardless"
# (kun for eksisterende GoCardless-kontoer; nye registreringer er stengt).
PROVIDER = os.getenv("PROVIDER", "enablebanking").strip().lower()

# --- GoCardless Bank Account Data ---
GC_SECRET_ID = os.getenv("GOCARDLESS_SECRET_ID", "").strip()
GC_SECRET_KEY = os.getenv("GOCARDLESS_SECRET_KEY", "").strip()
GC_BASE_URL = os.getenv(
    "GOCARDLESS_BASE_URL", "https://bankaccountdata.gocardless.com/api/v2"
).rstrip("/")

# --- Enable Banking ---
EB_APP_ID = os.getenv("ENABLEBANKING_APP_ID", "").strip()
# Tom verdi -> bruk standard sti relativt til prosjektroten (funker både i
# LXC (/opt/okonomi) og Docker (/app)).
EB_KEY_PATH = (os.getenv("ENABLEBANKING_KEY_PATH") or "").strip() or str(
    _ROOT / "data" / "enablebanking_private.pem"
)
EB_BASE_URL = os.getenv("ENABLEBANKING_BASE_URL", "https://api.enablebanking.com").rstrip("/")

# Land for banklisten (ISO 3166 alfa-2). Norge = "no".
COUNTRY = os.getenv("COUNTRY", "no").strip().lower()

# Hvor mange dager transaksjonshistorikk vi ber om ved ny tilkobling (maks 730,
# men de fleste norske banker gir 90).
ACCESS_DAYS = int(os.getenv("ACCESS_DAYS", "90"))
# Hvor langt tilbake vi ber banken om transaksjoner VED FØRSTE uttrekk for en
# konto (banken gir det den har – mest rett etter fersk BankID-innlogging).
HISTORY_DAYS = int(os.getenv("HISTORY_DAYS", "730"))
# Vindu for OPPFØLGINGS-synk når kontoen allerede har arkivdata. Enable Banking
# anbefaler å begrense date_from etter førstegangs-uttrekk: mange banker serverer
# kun ~90 dager uten fersk SCA, og lange ubetjente forespørsler er unødig tunge.
SYNC_RECENT_DAYS = int(os.getenv("SYNC_RECENT_DAYS", "30"))

# Offentlig URL appen nås på — brukes som redirect etter bankinnlogging.
# F.eks. http://192.168.1.50:8080 eller https://okonomi.hjemme.no
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8080").rstrip("/")

# --- Database ---
DB_PATH = os.getenv("DB_PATH", str(_ROOT / "data" / "okonomi.db"))

# --- Enkel tilgangsbeskyttelse (valgfritt) ---
# Settes APP_PASSWORD, kreves HTTP Basic Auth for hele appen.
APP_USER = os.getenv("APP_USER", "familien").strip()
APP_PASSWORD = os.getenv("APP_PASSWORD", "").strip()

# Hemmelig nøkkel for signering (settes i .env i produksjon).
SECRET_KEY = os.getenv("SECRET_KEY", "endre-meg-i-produksjon").strip()

# Minimum tid mellom automatiske synkroniseringer per konto (timer).
# GoCardless gratisnivå tillater ~4 uttrekk per konto per døgn.
SYNC_MIN_INTERVAL_HOURS = int(os.getenv("SYNC_MIN_INTERVAL_HOURS", "6"))

# Automatisk daglig synk (bakgrunnsjobb i appen). Sett AUTO_SYNC=0 for å skru av.
AUTO_SYNC = os.getenv("AUTO_SYNC", "1").strip() not in ("0", "false", "no", "")
# Klokketime (0–23, lokal servertid) for den daglige synken.
AUTO_SYNC_HOUR = int(os.getenv("AUTO_SYNC_HOUR", "6"))

# --- AI-analyse (valgfritt) ---
# Settes ANTHROPIC_API_KEY, får dashboardet en KI-drevet analyse som flagger
# avvik, mulige datafeil og gir konkrete råd. Uten nøkkel faller appen tilbake
# på den regelbaserte oppsummeringen – ingen data forlater serveren.
# PERSONVERN: kun aggregerte tall sendes ut (kategorisummer, inntekt/forbruk/
# budsjett/sparerate/lånerenter) – aldri enkelttransaksjoner, mottakere eller navn.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-5").strip()
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")


# Lett synk når du ÅPNER appen. Dette er et ekte betjent uttrekk (du er til stede),
# så bankene behandler det som brukerutløst – i motsetning til nattjobben, som
# noen banker (SPV/Sparebanken Norge) avviser for transaksjoner. Kontoer som ble
# synket for under SYNC_MIN_INTERVAL_HOURS siden hoppes over, så det maser ikke.
SYNC_ON_OPEN = _bool("SYNC_ON_OPEN", True)


def ai_configured() -> bool:
    return bool(ANTHROPIC_API_KEY)


def gocardless_configured() -> bool:
    return bool(GC_SECRET_ID and GC_SECRET_KEY)


def enablebanking_configured() -> bool:
    return bool(EB_APP_ID and os.path.exists(EB_KEY_PATH))


def provider_configured() -> bool:
    if PROVIDER == "gocardless":
        return gocardless_configured()
    return enablebanking_configured()
