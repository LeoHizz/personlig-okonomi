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


def archive(account_id: str, raw_objs: list, provider: str, fetched_at: str,
            statuses: list | None = None) -> int:
    """Lagre rå-transaksjonsobjekter uforanderlig, i ÉN transaksjon. Idempotent;
    returnerer antall NYE rader (eksisterende ignoreres, aldri overskrevet).

    `statuses` er en valgfri parallell-liste (samme rekkefølge som `raw_objs`) med
    bokført/ventende slik banken viste raden (BOOK/PDNG). Rå-objektet selv bærer
    ikke skillet, så vi lagrer det som egen kolonne – ikke som del av innholds-
    hashen (idempotens uendret). Avledet-laget teller kun bokførte.

    To genuint distinkte transaksjoner kan ha IDENTISK rå-innhold (f.eks. to like
    kjøp samme dag uten entry_reference). Vi disambiguerer da med en forekomst-
    teller PER hentebatch (base, base.1, base.2 …) – stabil på tvers av hentinger
    (samme batch => samme nøkler), så vi verken mister eller dubler dem."""
    seen: dict[str, int] = {}
    rows = []
    for i, r in enumerate(raw_objs or []):
        if not isinstance(r, dict):
            continue
        base = _content_hash(account_id, r)
        occ = seen.get(base, 0)
        seen[base] = occ + 1
        ch = base if occ == 0 else f"{base}.{occ}"
        status = statuses[i] if statuses is not None and i < len(statuses) else None
        rows.append((
            ch, account_id, provider, fetched_at,
            str(r.get("entry_reference") or "") or None,
            r.get("booking_date") or r.get("value_date") or r.get("transaction_date") or None,
            _amount(r), status,
            json.dumps(r, ensure_ascii=False, default=str),
        ))
    if not rows:
        return 0
    return db.insert_ignore_many(
        "INSERT OR IGNORE INTO raw_transactions"
        "(content_hash, account_id, provider, fetched_at, entry_reference, booking_date, amount, status, raw) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        rows,
    )


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
