"""Klient mot GoCardless Bank Account Data API (tidligere Nordigen).

Dokumentasjon: https://developer.gocardless.com/bank-account-data/
Gratis for kontoinformasjon (AIS). Nøkkelbegreper:
  - token: access (24t) + refresh (30 dager)
  - institution: en bank (har egen id, f.eks. SPAREBANKEN_VEST_SPTRNO22)
  - requisition: en samtykke-økt som knytter brukeren til én bank
  - account: en konto brukeren har gitt tilgang til (varer i 90 dager)
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import httpx

from . import config, db
from .errors import ProviderError

_TIMEOUT = httpx.Timeout(30.0)

# Del feiltype med resten av appen (beholdt navn for bakoverkompatibilitet)
GoCardlessError = ProviderError
Error = ProviderError


def _now() -> float:
    return time.time()


# --- token-håndtering (caches i settings-tabellen) ---

def _new_token() -> dict:
    if not config.gocardless_configured():
        raise GoCardlessError("GoCardless-nøkler mangler. Sett dem i .env.", status=400)
    resp = httpx.post(
        f"{config.GC_BASE_URL}/token/new/",
        json={"secret_id": config.GC_SECRET_ID, "secret_key": config.GC_SECRET_KEY},
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        raise GoCardlessError(
            "Kunne ikke hente token — sjekk GoCardless-nøklene.",
            status=resp.status_code,
            detail=_safe_json(resp),
        )
    data = resp.json()
    token = {
        "access": data["access"],
        "refresh": data["refresh"],
        "access_expires_at": _now() + int(data.get("access_expires", 86400)) - 60,
        "refresh_expires_at": _now() + int(data.get("refresh_expires", 2592000)) - 60,
    }
    db.set_setting("gc_token", token)
    return token


def _refresh_token(refresh: str) -> dict:
    resp = httpx.post(
        f"{config.GC_BASE_URL}/token/refresh/",
        json={"refresh": refresh},
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        # refresh utløpt -> lag helt ny
        return _new_token()
    data = resp.json()
    token = db.get_setting("gc_token", {}) or {}
    token["access"] = data["access"]
    token["access_expires_at"] = _now() + int(data.get("access_expires", 86400)) - 60
    db.set_setting("gc_token", token)
    return token


def _access_token() -> str:
    token = db.get_setting("gc_token")
    if not token:
        token = _new_token()
    elif token.get("access_expires_at", 0) < _now():
        if token.get("refresh_expires_at", 0) > _now():
            token = _refresh_token(token["refresh"])
        else:
            token = _new_token()
    return token["access"]


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_access_token()}",
        "Accept": "application/json",
    }


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return resp.text[:500]


def _request(method: str, path: str, **kwargs) -> httpx.Response:
    url = f"{config.GC_BASE_URL}{path}"
    resp = httpx.request(method, url, headers=_headers(), timeout=_TIMEOUT, **kwargs)
    if resp.status_code == 401:
        # token kan ha blitt ugyldig — tving ny og prøv én gang til
        _new_token()
        resp = httpx.request(method, url, headers=_headers(), timeout=_TIMEOUT, **kwargs)
    return resp


# --- API-kall ---

def list_institutions(country: str | None = None) -> list[dict]:
    country = country or config.COUNTRY
    resp = _request("GET", f"/institutions/?country={country}")
    if resp.status_code != 200:
        raise GoCardlessError(
            "Kunne ikke hente banklisten.", resp.status_code, _safe_json(resp)
        )
    return resp.json()


def create_requisition(institution_id: str, reference: str) -> dict:
    body = {
        "redirect": f"{config.APP_BASE_URL}/api/callback",
        "institution_id": institution_id,
        "reference": reference,
        "user_language": "NO",
    }
    resp = _request("POST", "/requisitions/", json=body)
    if resp.status_code not in (200, 201):
        raise GoCardlessError(
            "Kunne ikke opprette tilkobling til banken.",
            resp.status_code,
            _safe_json(resp),
        )
    return resp.json()


def get_requisition(requisition_id: str) -> dict:
    resp = _request("GET", f"/requisitions/{requisition_id}/")
    if resp.status_code != 200:
        raise GoCardlessError(
            "Kunne ikke hente tilkoblingen.", resp.status_code, _safe_json(resp)
        )
    return resp.json()


def _get_account_metadata(account_id: str) -> dict:
    resp = _request("GET", f"/accounts/{account_id}/")
    return resp.json() if resp.status_code == 200 else {}


def _get_account_details_raw(account_id: str) -> dict:
    resp = _request("GET", f"/accounts/{account_id}/details/")
    if resp.status_code == 200:
        return resp.json().get("account", {})
    return {}


# ---------------------------------------------------------------------------
#  Normalisert, leverandør-uavhengig grensesnitt (brukt av provider.py)
# ---------------------------------------------------------------------------

def start_authorization(institution_id: str, reference: str) -> dict:
    req = create_requisition(institution_id, reference)
    return {"id": req["id"], "url": req["link"]}


def finalize_authorization(query: dict) -> dict:
    """GoCardless-callback gir ingen id — finn nyeste requisition med kontoer."""
    rows = db.query("SELECT id FROM requisitions ORDER BY created_at DESC LIMIT 5")
    for r in rows:
        try:
            info = get_requisition(r["id"])
        except GoCardlessError:
            continue
        if info.get("accounts"):
            inst = info.get("institution_id", "")
            accounts = []
            for acc_id in info["accounts"]:
                d = _get_account_details_raw(acc_id)
                m = _get_account_metadata(acc_id)
                accounts.append(
                    {
                        "id": acc_id,
                        "iban": d.get("iban") or m.get("iban", ""),
                        "name": d.get("name") or d.get("product") or d.get("ownerName") or "Konto",
                        "currency": d.get("currency", "NOK"),
                        "product": d.get("product", ""),
                    }
                )
            return {
                "connection_id": r["id"],
                "institution_id": inst,
                "institution_name": inst,
                "accounts": accounts,
            }
    return {"connection_id": "", "institution_id": "", "institution_name": "", "accounts": []}


def get_account_details(account_id: str) -> dict:
    d = _get_account_details_raw(account_id)
    m = _get_account_metadata(account_id)
    return {
        "id": account_id,
        "iban": d.get("iban") or m.get("iban", ""),
        "name": d.get("name") or d.get("product") or "Konto",
        "currency": d.get("currency", "NOK"),
        "product": d.get("product", ""),
    }


_BALANCE_TYPE_MAP = {
    "closingBooked": "closing", "interimBooked": "closing",
    "interimAvailable": "available", "forwardAvailable": "available",
    "openingBooked": "opening", "expected": "expected",
}


def get_balances(account_id: str) -> list[dict]:
    resp = _request("GET", f"/accounts/{account_id}/balances/")
    if resp.status_code == 429:
        raise GoCardlessError("Ratebegrensning nådd (saldo).", 429, _safe_json(resp))
    if resp.status_code != 200:
        return []
    out = []
    for b in resp.json().get("balances", []) or []:
        amt = b.get("balanceAmount", {}) or {}
        try:
            amount = float(amt.get("amount", 0))
        except (TypeError, ValueError):
            amount = 0.0
        out.append(
            {
                "amount": amount,
                "currency": amt.get("currency", "NOK"),
                "type": _BALANCE_TYPE_MAP.get(b.get("balanceType", ""), "other"),
                "date": b.get("referenceDate", ""),
            }
        )
    return out


def get_transactions(account_id: str, date_from: str | None = None) -> list[dict]:
    path = f"/accounts/{account_id}/transactions/"
    if date_from:
        path += f"?date_from={date_from}"
    resp = _request("GET", path)
    if resp.status_code == 429:
        raise GoCardlessError("Ratebegrensning nådd (transaksjoner). Prøv igjen senere.", 429, _safe_json(resp))
    if resp.status_code != 200:
        raise GoCardlessError("Kunne ikke hente transaksjoner.", resp.status_code, _safe_json(resp))
    block = resp.json().get("transactions", {}) or {}
    out = []
    for status in ("booked", "pending"):
        for t in block.get(status, []) or []:
            amt = t.get("transactionAmount", {}) or {}
            try:
                amount = float(amt.get("amount", 0))
            except (TypeError, ValueError):
                amount = 0.0
            counterparty = (t.get("creditorName") or t.get("debtorName") or "").strip()
            rem = t.get("remittanceInformationUnstructured")
            if not rem:
                arr = t.get("remittanceInformationUnstructuredArray")
                rem = " ".join(arr) if isinstance(arr, list) else ""
            out.append(
                {
                    "id": t.get("transactionId") or t.get("internalTransactionId") or None,
                    "booking_date": t.get("bookingDate") or t.get("valueDate"),
                    "value_date": t.get("valueDate") or t.get("bookingDate"),
                    "amount": amount,
                    "currency": amt.get("currency", "NOK"),
                    "counterparty": counterparty,
                    "remittance": (rem or "").strip(),
                    "status": status,
                }
            )
    return out


def normalize_raw(raw_obj: dict) -> dict:
    """Offentlig: normaliser et lagret rå-objekt til app-formatet. GoCardless-arkivet
    lagrer allerede normaliserte objekter, så dette er i praksis gjennomslag (defensivt
    – sikrer at deriver-laget har et provider-uavhengig inntak)."""
    return dict(raw_obj)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
