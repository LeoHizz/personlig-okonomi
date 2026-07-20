"""Kilde-lag: rått, urørt arkiv av bankens transaksjonsobjekter.

Prinsipp: hent komplette rådata fra banken og lagre dem verbatim og uforanderlig.
Alt appen viser (kategorier, saldoer, analyse) deriveres FRA dette laget – aldri
omvendt. Da har vi alltid fasit på hva banken faktisk ga oss, og kan bygge om
tolkningen uten å røre kilden eller spørre banken på nytt.
"""
from __future__ import annotations

import hashlib
import json

from . import db


def _content_hash(account_id: str, raw_obj) -> str:
    """Stabil hash av hele rå-objektet (sortert JSON) – samme objekt => samme rad."""
    basis = account_id + "|" + json.dumps(raw_obj, sort_keys=True, ensure_ascii=False, default=str)
    return "r_" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:28]


def _amount(raw_obj) -> float | None:
    """Trekk ut beløp KUN for indeks/søk (rå-objektet er fasit)."""
    amt = raw_obj.get("transaction_amount") or raw_obj.get("amount")
    if isinstance(amt, dict):
        amt = amt.get("amount")
    try:
        return float(amt)
    except (TypeError, ValueError):
        return None


def archive(account_id: str, raw_objs: list, provider: str, fetched_at: str) -> int:
    """Lagre rå-transaksjonsobjekter uforanderlig. Idempotent på innholds-hash;
    returnerer antall NYE rader (eksisterende ignoreres, aldri overskrevet)."""
    new = 0
    for r in raw_objs or []:
        if not isinstance(r, dict):
            continue
        ch = _content_hash(account_id, r)
        cur = db.execute_rowcount(
            "INSERT OR IGNORE INTO raw_transactions"
            "(content_hash, account_id, provider, fetched_at, entry_reference, booking_date, amount, raw) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                ch, account_id, provider, fetched_at,
                str(r.get("entry_reference") or "") or None,
                r.get("booking_date") or r.get("value_date") or r.get("transaction_date") or None,
                _amount(r),
                json.dumps(r, ensure_ascii=False, default=str),
            ),
        )
        new += cur
    return new


def record_run(account_id: str, status: str, at: str,
               http_status: int | None = None, count: int = 0,
               error_detail: str | None = None) -> None:
    """Logg utfallet av et synk-forsøk (ok/error) – gjør feil synlige."""
    db.execute(
        "INSERT INTO sync_runs(account_id, started_at, status, http_status, count, error_detail) "
        "VALUES(?,?,?,?,?,?)",
        (account_id, at, status, http_status, count, error_detail),
    )


def last_runs(limit: int = 20) -> list[dict]:
    return [dict(r) for r in db.query(
        "SELECT account_id, started_at, status, http_status, count, error_detail "
        "FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,))]


def stats() -> dict:
    n = db.query("SELECT COUNT(*) AS n FROM raw_transactions")[0]["n"]
    by_acc = db.query(
        "SELECT account_id, COUNT(*) AS n, MIN(booking_date) AS first, MAX(booking_date) AS last "
        "FROM raw_transactions GROUP BY account_id"
    )
    return {"total": n, "by_account": [dict(r) for r in by_acc]}
