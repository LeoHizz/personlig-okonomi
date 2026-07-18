"""Synkronisering: henter kontoer, saldo og transaksjoner fra valgt leverandør
(Enable Banking eller GoCardless) og lagrer dem i den lokale databasen.
Dashboardet leser alltid fra databasen.

Ratebegrensning: leverandørene begrenser antall uttrekk per konto per døgn, så vi
hopper over kontoer som ble synket nylig med mindre `force=True`.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from . import categorize, config, db, provider as gc


def _hash_tx(account_id: str, t: dict) -> str:
    basis = f"{account_id}|{t.get('booking_date')}|{t.get('amount')}|{t.get('remittance')}"
    return "h_" + hashlib.sha1(basis.encode()).hexdigest()[:20]


def _bank_code(institution_name: str, institution_id: str) -> str:
    name = (institution_name or institution_id or "").upper()
    if "SPAREBANKEN" in name or "SPV" in name or name.startswith("SPARE"):
        return "SPV"
    if "DNB" in name:
        return "DNB"
    if "COOP" in name:
        return "COOP"
    if "NORDEA" in name:
        return "NORDEA"
    if "SANTANDER" in name:
        return "SANT"
    if "HANDELSBANKEN" in name:
        return "HB"
    if "SBANKEN" in name:
        return "SB"
    return (institution_name or "BANK")[:4].upper()


def _save_balances(account_id: str, balances: list[dict]) -> None:
    db.execute("DELETE FROM balances WHERE account_id = ?", (account_id,))
    for b in balances:
        db.execute(
            "INSERT OR REPLACE INTO balances(account_id, balance_type, amount, currency, reference_date) "
            "VALUES(?,?,?,?,?)",
            (account_id, b.get("type", "other"), b.get("amount", 0.0),
             b.get("currency", "NOK"), b.get("date", "")),
        )


def _upsert_transactions(account_id: str, txs: list[dict]) -> int:
    count = 0
    for t in txs:
        tx_id = t.get("id") or _hash_tx(account_id, t)
        counterparty = t.get("counterparty", "")
        remittance = t.get("remittance", "")
        amount = t.get("amount", 0.0)

        existing = db.query(
            "SELECT category, category_source FROM transactions WHERE id = ?", (tx_id,)
        )
        if existing and existing[0]["category_source"] == "manual":
            category, source = existing[0]["category"], "manual"
        else:
            category = categorize.categorize(counterparty, remittance, amount)
            source = "auto"

        db.upsert(
            "transactions",
            {
                "id": tx_id,
                "account_id": account_id,
                "booking_date": t.get("booking_date"),
                "value_date": t.get("value_date") or t.get("booking_date"),
                "amount": amount,
                "currency": t.get("currency", "NOK"),
                "counterparty": counterparty,
                "remittance": remittance,
                "category": category,
                "category_source": source,
                "status": t.get("status", "booked"),
                "raw": json.dumps(t, ensure_ascii=False),
            },
        )
        count += 1
    return count


def register_accounts(query: dict) -> list[str]:
    """Etter fullført banksamtykke: hent kontoene og lagre metadata."""
    res = gc.finalize_authorization(query)
    conn_id = res.get("connection_id", "")
    inst_id = res.get("institution_id", "")
    inst_name = res.get("institution_name", inst_id)
    code = _bank_code(inst_name, inst_id)

    if conn_id:
        db.upsert(
            "requisitions",
            {
                "id": conn_id,
                "institution_id": inst_id,
                "institution_name": inst_name,
                "reference": query.get("state") or query.get("ref") or "",
                "status": "LN",
                "link": "",
                "created_at": gc.utc_now_iso(),
            },
        )

    ids = []
    for acc in res.get("accounts", []):
        acc_id = acc["id"]
        iban = acc.get("iban", "")
        ids.append(acc_id)
        # Enable Banking gir nye konto-ID-er ved re-tilkobling. Arv navn/eier/etikett
        # fra en tidligere konto med samme kontonummer (IBAN), så mappingen bevares.
        existing = db.query("SELECT name, owner, bank_code FROM accounts WHERE id = ?", (acc_id,))
        prior = []
        if not existing and iban:
            prior = db.query(
                "SELECT name, owner, bank_code FROM accounts WHERE iban = ? ORDER BY hidden ASC LIMIT 1",
                (iban,),
            )
        src = existing[0] if existing else (prior[0] if prior else None)
        keep_name = src["name"] if src else acc.get("name", "Konto")
        keep_owner = src["owner"] if src else "Felles"
        keep_code = src["bank_code"] if src else code
        db.upsert(
            "accounts",
            {
                "id": acc_id,
                "requisition_id": conn_id,
                "institution_id": inst_id,
                "institution_name": inst_name,
                "bank_code": keep_code,
                "iban": iban,
                "name": keep_name,
                "owner": keep_owner,
                "currency": acc.get("currency", "NOK"),
                "product": acc.get("product", ""),
                "status": "READY",
                "hidden": 0,
                "created_at": gc.utc_now_iso(),
            },
        )
        # Skjul eldre duplikater med samme kontonummer (unngår at lista vokser).
        if iban:
            db.execute("UPDATE accounts SET hidden = 1 WHERE iban = ? AND id != ?", (iban, acc_id))
    return ids


def sync_account(account_id: str, force: bool = False) -> dict:
    row = db.query("SELECT last_synced FROM accounts WHERE id = ?", (account_id,))
    if row and row[0]["last_synced"] and not force:
        try:
            last_dt = datetime.fromisoformat(row[0]["last_synced"])
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - last_dt < timedelta(hours=config.SYNC_MIN_INTERVAL_HOURS):
                return {"account_id": account_id, "skipped": True, "reason": "nylig synket"}
        except ValueError:
            pass

    result = {"account_id": account_id, "transactions": 0, "skipped": False}
    try:
        _save_balances(account_id, gc.get_balances(account_id))
    except gc.Error as e:
        result["balance_error"] = str(e)

    date_from = (datetime.now(timezone.utc) - timedelta(days=config.HISTORY_DAYS)).date().isoformat()
    txs = gc.get_transactions(account_id, date_from=date_from)
    result["transactions"] = _upsert_transactions(account_id, txs)

    db.execute("UPDATE accounts SET last_synced = ? WHERE id = ?", (gc.utc_now_iso(), account_id))
    return result


def sync_all(force: bool = False) -> dict:
    accounts = db.query("SELECT id FROM accounts WHERE hidden = 0 AND institution_id != 'csv-import'")
    results = []
    for a in accounts:
        try:
            results.append(sync_account(a["id"], force=force))
        except gc.Error as e:
            results.append({"account_id": a["id"], "error": str(e), "status": getattr(e, "status", None)})
    db.set_setting("last_sync_at", gc.utc_now_iso())
    return {"synced": results, "at": gc.utc_now_iso()}
