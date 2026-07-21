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

from . import categorize, config, db, provider as gc, rawstore


def _hash_tx(account_id: str, t: dict) -> str:
    basis = f"{account_id}|{t.get('booking_date')}|{t.get('amount')}|{t.get('remittance')}"
    return "h_" + hashlib.sha1(basis.encode()).hexdigest()[:20]


def _tx_id(account_id: str, t: dict) -> str:
    """Stabil, kollisjonsfri transaksjons-id. Inkluderer booking_date fordi bankens
    entry_reference kan GJENTAS (f.eks. faste lånetrekk der referansen = lånekonto-
    nummeret, likt hver måned). Uten datoen kollapser alle månedene til én rad."""
    ref = t.get("id")
    if not ref:
        return _hash_tx(account_id, t)
    bd = t.get("booking_date") or ""
    # Uten dato: IKKE etterlat en avsluttende «:» – da ville live-synk skrive
    # «A:ref:» mens engangs-migreringen (som hopper over tomme datoer) beholder
    # «A:ref», og de to id-ene ville kollidere til hver sin rad (duplikat).
    return f"{account_id}:{ref}:{bd}" if bd else f"{account_id}:{ref}"


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
    # Aldri slett eksisterende saldo på et tomt svar (feil/ratebegrensning/utilgjengelig
    # i ny økt) – da ville en mislykket synk «tømme» en konto som egentlig er i orden.
    if not balances:
        return
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
        tx_id = _tx_id(account_id, t)
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
                "raw": json.dumps(t.get("raw", t), ensure_ascii=False),
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
    all_accts = db.query("SELECT * FROM accounts")
    by_number = {}
    for r in all_accts:
        no = db.account_number(r["iban"], r["bban"])
        if no:
            by_number.setdefault(no, r)
    for acc in res.get("accounts", []):
        uid = acc["id"]  # bankens økt-ID (uid) – kun for API-kall
        iban = acc.get("iban", "")
        bban = acc.get("bban", "")
        acctno = db.account_number(iban, bban)
        # STABIL id: kontonummeret. Da oppdaterer re-tilkobling SAMME konto
        # (uansett hvem som logger inn / hvilken økt), i stedet for å lage duplikat.
        stable_id = ("eb:" + acctno) if acctno else uid
        # Match på kontonummer først; fall alltid tilbake til stabil-id (samme rad vi
        # er i ferd med å overskrive) så eier/etikett arves selv om kontonummeret
        # mangler/er blanket – ellers nullstilles de ved re-tilkobling.
        prior = (by_number.get(acctno) if acctno else None) or \
            next((r for r in all_accts if r["id"] == stable_id), None)
        keep_name = prior["name"] if prior else acc.get("name", "Konto")
        keep_owner = prior["owner"] if prior else "Felles"
        keep_code = prior["bank_code"] if prior else code
        keep_hidden = prior["hidden"] if prior else 0
        db.upsert(
            "accounts",
            {
                "id": stable_id,
                "provider_ref": uid,
                "requisition_id": conn_id,
                "institution_id": inst_id,
                "institution_name": inst_name,
                "bank_code": keep_code,
                "iban": iban,
                "bban": bban,
                "name": keep_name,
                "owner": keep_owner,
                "currency": acc.get("currency", "NOK"),
                "product": acc.get("product", ""),
                "status": "READY",
                "hidden": keep_hidden,
                "created_at": (prior["created_at"] if prior else gc.utc_now_iso()),
            },
        )
        ids.append(stable_id)
    return ids


def sync_account(account_id: str, force: bool = False) -> dict:
    row = db.query("SELECT last_synced, provider_ref FROM accounts WHERE id = ?", (account_id,))
    # Bankens økt-ID for selve API-kallene; kontoen (account_id) er stabil.
    ref = (row[0]["provider_ref"] if row and row[0]["provider_ref"] else account_id)
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
        _save_balances(account_id, gc.get_balances(ref))
    except gc.Error as e:
        result["balance_error"] = str(e)

    date_from = (datetime.now(timezone.utc) - timedelta(days=config.HISTORY_DAYS)).date().isoformat()
    now = gc.utc_now_iso()
    try:
        txs = gc.get_transactions(ref, date_from=date_from)
    except gc.Error as e:
        # Feil på bokførte transaksjoner logges (ikke svelges) – synk «lyver» ikke lenger.
        rawstore.record_run(account_id, "error", now, getattr(e, "status", None), 0, str(e))
        result["tx_error"] = str(e)
    else:
        # 1) KILDE: arkiver ALT banken ga oss, urørt, med bokført/ventende-status.
        raw_objs = [t.get("raw", t) for t in txs]
        statuses = [t.get("status") for t in txs]
        result["raw_new"] = rawstore.archive(account_id, raw_objs, config.PROVIDER, now, statuses)
        rawstore.record_run(account_id, "ok", now, 200, len(raw_objs))
        # 2) AVLEDET: kun BOKFØRTE teller. Ventende (pending) skifter dato/beløp/referanse
        # når de bokføres → ville blitt en NY rad = dobbeltføring. De hentes uansett inn
        # på nytt som bokførte (1–3 dager). Vi ofrer «ferskest mulig» for korrekte tall.
        booked = [t for t in txs if t.get("status") != "pending"]
        result["pending_skipped"] = len(txs) - len(booked)
        result["transactions"] = _upsert_transactions(account_id, booked)

    db.execute("UPDATE accounts SET last_synced = ? WHERE id = ?", (now, account_id))
    return result


def sync_all(force: bool = False) -> dict:
    accounts = db.query("SELECT id FROM accounts WHERE hidden = 0 AND institution_id != 'csv-import'")
    results = []
    for a in accounts:
        try:
            results.append(sync_account(a["id"], force=force))
        except gc.Error as e:
            results.append({"account_id": a["id"], "error": str(e), "status": getattr(e, "status", None)})
    # Stemple «sist synket» kun hvis minst én konto faktisk lyktes – ikke lyv om
    # ferskhet når alt feilet (ellers viser statusen et falskt friskt tidspunkt).
    if any(not r.get("error") and not r.get("tx_error") for r in results):
        db.set_setting("last_sync_at", gc.utc_now_iso())
    return {"synced": results, "at": gc.utc_now_iso()}


def rebuild_from_raw() -> dict:
    """AVLEDET-lag: bygg arbeidstabellen `transactions` på nytt FRA kilde-arkivet
    (raw_transactions). Ingen API-kall – gjenkjørbar når kategori-/tolknings-logikk
    endres. Bevarer manuelle kategorier og per-transaksjon-merkelapper. Rører ikke
    CSV-importerte rader (de ligger ikke i bank-arkivet). Ikke-destruktiv/refresh:
    upserter fra arkivet, sletter ikke (arkivet er fasit først etter komplett backfill)."""
    # Bevar brukerens overstyringer, nøklet på tx-id.
    keep_cat, keep_labels = {}, {}
    for r in db.query("SELECT id, category, category_source, labels FROM transactions"):
        if r["category_source"] == "manual":
            keep_cat[r["id"]] = r["category"]
        if r["labels"] and r["labels"] not in ("", "null", "[]"):
            keep_labels[r["id"]] = r["labels"]

    records = []
    for row in db.query("SELECT account_id, status, raw FROM raw_transactions ORDER BY booking_date"):
        # Ventende teller ikke (unngår dobbeltføring pending→booked). NULL-status =
        # eldre arkiv-rader vi ikke kjenner skillet på → behandles som bokført.
        if row["status"] == "pending":
            continue
        try:
            obj = json.loads(row["raw"])
        except (ValueError, TypeError):
            continue
        account_id = row["account_id"]
        t = gc.normalize_raw(obj)  # provider-uavhengig normalisering (ikke privat funksjon)
        tx_id = _tx_id(account_id, t)

        if tx_id in keep_cat:
            category, source = keep_cat[tx_id], "manual"
        else:
            category = categorize.categorize(t.get("counterparty", ""), t.get("remittance", ""), t.get("amount", 0.0))
            source = "auto"

        records.append({
            "id": tx_id, "account_id": account_id,
            "booking_date": t.get("booking_date"),
            "value_date": t.get("value_date") or t.get("booking_date"),
            "amount": t.get("amount", 0.0), "currency": t.get("currency", "NOK"),
            "counterparty": t.get("counterparty", ""), "remittance": t.get("remittance", ""),
            "category": category, "category_source": source,
            "status": t.get("status", "booked"),
            "raw": json.dumps(obj, ensure_ascii=False),
            "labels": keep_labels.get(tx_id),   # None hvis ingen – uniform kolonner for batch
        })
    db.upsert_many("transactions", records)  # ÉN transaksjon
    return {"rebuilt": len(records)}
