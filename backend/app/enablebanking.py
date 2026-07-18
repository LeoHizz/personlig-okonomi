"""Adapter mot Enable Banking (https://enablebanking.com).

Gratis for personlig bruk (egne kontoer), god dekning av norske/nordiske banker.
Autentisering: JWT signert med RS256 (din private RSA-nøkkel), kid = App-ID.

Dette modulet eksponerer det leverandør-uavhengige grensesnittet som resten av
appen bruker (se provider.py). Alle funksjoner returnerer normaliserte strukturer.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx

from . import config
from .errors import ProviderError

# Del feiltype med resten av appen
Error = ProviderError

_TIMEOUT = httpx.Timeout(60.0)
_token_cache: dict = {}


# --- JWT / autentisering ---

def _jwt() -> str:
    cached = _token_cache.get("jwt")
    if cached and cached[1] > time.time():
        return cached[0]
    try:
        import jwt  # PyJWT
    except ImportError as e:  # pragma: no cover
        raise ProviderError("PyJWT mangler (pip install 'pyjwt[crypto]').", 500) from e
    if not config.EB_APP_ID:
        raise ProviderError("ENABLEBANKING_APP_ID er ikke satt i .env.", 400)
    try:
        with open(config.EB_KEY_PATH, "rb") as f:
            private_key = f.read()
    except OSError as e:
        raise ProviderError(
            f"Fant ikke privat nøkkel på {config.EB_KEY_PATH}.", 400
        ) from e
    now = int(time.time())
    payload = {
        "iss": "enablebanking.com",
        "aud": "api.enablebanking.com",
        "iat": now,
        "exp": now + 3600,
    }
    token = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": config.EB_APP_ID})
    if isinstance(token, bytes):
        token = token.decode()
    _token_cache["jwt"] = (token, now + 3300)
    return token


def _headers() -> dict:
    return {"Authorization": f"Bearer {_jwt()}", "Content-Type": "application/json"}


def _safe_json(resp: httpx.Response):
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return resp.text[:500]


def _request(method: str, path: str, **kwargs) -> httpx.Response:
    url = f"{config.EB_BASE_URL}{path}"
    return httpx.request(method, url, headers=_headers(), timeout=_TIMEOUT, **kwargs)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_until() -> str:
    dt = (datetime.now(timezone.utc) + timedelta(days=config.ACCESS_DAYS)).replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


# --- banker ---

def list_institutions() -> list[dict]:
    resp = _request("GET", f"/aspsps?country={config.COUNTRY.upper()}&psu_type=personal")
    if resp.status_code != 200:
        raise ProviderError("Kunne ikke hente banklisten.", resp.status_code, _safe_json(resp))
    data = resp.json()
    seen = set()
    banks = []
    for a in data.get("aspsps", []):
        name = a.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        banks.append({"id": name, "name": name, "logo": a.get("logo", "")})
    return banks


# --- samtykke-flyt ---

def start_authorization(institution_id: str, reference: str) -> dict:
    body = {
        "access": {"valid_until": _valid_until()},
        "aspsp": {"name": institution_id, "country": config.COUNTRY.upper()},
        "state": reference,
        "redirect_url": f"{config.APP_BASE_URL}/api/callback",
        "psu_type": "personal",
    }
    resp = _request("POST", "/auth", json=body)
    if resp.status_code not in (200, 201):
        raise ProviderError(
            "Kunne ikke starte tilkobling til banken.", resp.status_code, _safe_json(resp)
        )
    data = resp.json()
    return {"id": data.get("authorization_id") or data.get("session_id") or reference,
            "url": data["url"]}


def finalize_authorization(query: dict) -> dict:
    code = query.get("code")
    if not code:
        raise ProviderError("Mangler 'code' fra banken.", 400, query)
    resp = _request("POST", "/sessions", json={"code": code})
    if resp.status_code not in (200, 201):
        raise ProviderError("Kunne ikke fullføre tilkoblingen.", resp.status_code, _safe_json(resp))
    session = resp.json()
    aspsp = session.get("aspsp", {}) or {}
    inst_name = aspsp.get("name", "")
    accounts = []
    for item in session.get("accounts", []) or []:
        if isinstance(item, str):
            uid = item
            details = _account_details(uid)
        else:
            uid = item.get("uid") or item.get("account_id", {}).get("iban") or ""
            details = item
        accounts.append(_normalize_account(uid, details))
    return {
        "connection_id": session.get("session_id", ""),
        "institution_id": inst_name,
        "institution_name": inst_name,
        "accounts": accounts,
    }


def _account_details(uid: str) -> dict:
    resp = _request("GET", f"/accounts/{uid}/details")
    return resp.json() if resp.status_code == 200 else {}


def _normalize_account(uid: str, details: dict) -> dict:
    acc_id = details.get("account_id", {}) or {}
    return {
        "id": uid,
        "iban": acc_id.get("iban", "") if isinstance(acc_id, dict) else "",
        "name": details.get("name") or details.get("product") or "Konto",
        "currency": details.get("currency", "NOK"),
        "product": details.get("product", ""),
    }


def get_account_details(account_id: str) -> dict:
    return _normalize_account(account_id, _account_details(account_id))


# --- saldo ---

_BALANCE_TYPE_MAP = {
    "CLBD": "closing", "ITBD": "closing", "PRCD": "other",
    "CLAV": "available", "ITAV": "available", "FWAV": "available",
    "OPBD": "opening", "XPCD": "expected", "OTHR": "other", "INFO": "other",
}


def get_balances(account_id: str) -> list[dict]:
    resp = _request("GET", f"/accounts/{account_id}/balances")
    if resp.status_code == 429:
        raise ProviderError("Ratebegrensning nådd (saldo).", 429, _safe_json(resp))
    if resp.status_code != 200:
        return []
    out = []
    for b in resp.json().get("balances", []) or []:
        amt = b.get("balance_amount", {}) or {}
        try:
            amount = float(amt.get("amount", 0))
        except (TypeError, ValueError):
            amount = 0.0
        out.append(
            {
                "amount": amount,
                "currency": amt.get("currency", "NOK"),
                "type": _BALANCE_TYPE_MAP.get(b.get("balance_type", ""), "other"),
                "date": b.get("reference_date") or (b.get("last_change_date_time") or "")[:10],
            }
        )
    return out


# --- transaksjoner ---

def get_transactions(account_id: str, date_from: str | None = None) -> list[dict]:
    out: list[dict] = []
    for status_filter, status_norm in (("BOOK", "booked"), ("PDNG", "pending")):
        cont = None
        for _ in range(20):  # sikkerhetsgrense mot uendelig paginering
            params = {"transaction_status": status_filter}
            if date_from and status_filter == "BOOK":
                params["date_from"] = date_from
            if cont:
                params["continuation_key"] = cont
            resp = _request("GET", f"/accounts/{account_id}/transactions", params=params)
            if resp.status_code == 429:
                raise ProviderError("Ratebegrensning nådd (transaksjoner).", 429, _safe_json(resp))
            if resp.status_code != 200:
                # pending støttes ikke av alle banker – hopp over stille
                break
            data = resp.json()
            for t in data.get("transactions", []) or []:
                out.append(_normalize_tx(t, status_norm))
            cont = data.get("continuation_key")
            if not cont:
                break
    return out


def _normalize_tx(t: dict, status_norm: str) -> dict:
    amt = t.get("transaction_amount", {}) or {}
    try:
        amount = float(amt.get("amount", 0))
    except (TypeError, ValueError):
        amount = 0.0
    if t.get("credit_debit_indicator") == "DBIT":
        amount = -amount
        counterparty = (t.get("creditor", {}) or {}).get("name", "")
    else:
        counterparty = (t.get("debtor", {}) or {}).get("name", "")
    rem = t.get("remittance_information") or []
    remittance = " ".join(rem) if isinstance(rem, list) else str(rem)
    return {
        "id": t.get("entry_reference") or None,
        "booking_date": t.get("booking_date") or t.get("value_date") or t.get("transaction_date"),
        "value_date": t.get("value_date") or t.get("booking_date"),
        "amount": amount,
        "currency": amt.get("currency", "NOK"),
        "counterparty": (counterparty or "").strip(),
        "remittance": remittance.strip(),
        "status": status_norm,
    }
