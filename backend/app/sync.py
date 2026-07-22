"""Synkronisering: henter kontoer, saldo og transaksjoner fra valgt leverandør
(Enable Banking eller GoCardless) og lagrer dem i den lokale databasen.
Dashboardet leser alltid fra databasen.

Ratebegrensning: leverandørene begrenser antall uttrekk per konto per døgn, så vi
hopper over kontoer som ble synket nylig med mindre `force=True`.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from . import categorize, config, db, provider as gc, rawstore


def apply_loan_transfers() -> int:
    """Marker lånebetalinger som «Overføring» så de IKKE telles som forbruk. Et
    lånetrekk er avdrag (sparing) + rente – renten telles separat via amortiseringen
    (Lånerenter). Kjennes robust igjen på lånets «pay_match» (kontonr./tekst) mot
    remittance/motpart ELLER entry_reference. Rører aldri manuelt satte kategorier."""
    liabs = db.get_setting("manual_liabilities", []) or []
    pats = [(lb.get("pay_match") or "").strip().lower() for lb in liabs if lb.get("auto")]
    total = 0
    for pat in [p for p in pats if p]:
        like = f"%{pat}%"
        total += db.execute_rowcount(
            "UPDATE transactions SET category = 'Overføring' "
            "WHERE amount < 0 AND category_source != 'manual' AND category != 'Overføring' "
            "AND (lower(remittance) LIKE ? OR lower(counterparty) LIKE ? OR lower(entry_reference) LIKE ?)",
            (like, like, like),
        )
    return total


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
        result["tx_status"] = getattr(e, "status", None)
    else:
        # 1) KILDE: arkiver ALT banken ga oss, urørt, med bokført/ventende-status.
        raw_objs = [t.get("raw", t) for t in txs]
        statuses = [t.get("status") for t in txs]
        result["raw_new"] = rawstore.archive(account_id, raw_objs, config.PROVIDER, now, statuses)
        rawstore.record_run(account_id, "ok", now, 200, len(raw_objs))
        # 2) AVLEDET: speil kontoen fra arkivet. Én id-vei (content_hash) → aldri kollisjon
        # selv når banken gjenbruker entry_reference. Ventende ekskluderes, lån=overføring
        # markeres. Ventende skifter dato/beløp/ref når de bokføres → holdes utenfor.
        result["pending_skipped"] = sum(1 for t in txs if t.get("status") == "pending")
        result["transactions"] = rebuild_from_raw(account_id).get("rebuilt", 0)

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


def rebuild_from_raw(account_id: str | None = None) -> dict:
    """AVLEDET-lag: bygg arbeidstabellen som et EKSAKT SPEIL av kilde-arkivet.
    Arbeidstabellens id = arkivets `content_hash` (garantert unik per transaksjon),
    så identiske/gjenbrukte bank-referanser (f.eks. SPVs entry_reference='21' på mange
    kjøp samme dag) ALDRI kolliderer. Ventende ekskluderes. Manuelle kategorier/
    merkelapper bevares nøklet på INNHOLD (konto+dato+beløp+tekst), ikke på id – så de
    overlever id-skiftet og tidligere kollapsede rader. CSV-import røres ikke (ligger
    ikke i arkivet). Kan skopes til én konto (brukes av live-synk).
    Ingen API-kall – gjenkjørbar."""
    scope = "WHERE account_id = ?" if account_id else ""
    params = (account_id,) if account_id else ()

    keep_cat, keep_labels = {}, {}
    for r in db.query(
        f"SELECT account_id, booking_date, amount, remittance, category, category_source, labels "
        f"FROM transactions {scope}", params):
        sig = (r["account_id"], r["booking_date"], r["amount"], r["remittance"])
        if r["category_source"] == "manual":
            keep_cat[sig] = r["category"]
        if r["labels"] and r["labels"] not in ("", "null", "[]"):
            keep_labels[sig] = r["labels"]

    records = []
    for row in db.query(
        f"SELECT content_hash, account_id, status, raw FROM raw_transactions {scope} "
        f"ORDER BY booking_date", params):
        if row["status"] == "pending":  # NULL-status (eldre) = behandles som bokført
            continue
        try:
            obj = json.loads(row["raw"])
        except (ValueError, TypeError):
            continue
        aid = row["account_id"]
        t = gc.normalize_raw(obj)
        bd = t.get("booking_date")
        amount = t.get("amount", 0.0)
        remittance = t.get("remittance", "")
        sig = (aid, bd, amount, remittance)
        if sig in keep_cat:
            category, source = keep_cat[sig], "manual"
        else:
            category = categorize.categorize(t.get("counterparty", ""), remittance, amount)
            source = "auto"
        records.append({
            "id": row["content_hash"],           # unik per arkivert transaksjon
            "account_id": aid,
            "entry_reference": t.get("id"),      # bankens (evt. gjenbrukte) referanse – for lån-match
            "booking_date": bd,
            "value_date": t.get("value_date") or bd,
            "amount": amount, "currency": t.get("currency", "NOK"),
            "counterparty": t.get("counterparty", ""), "remittance": remittance,
            "category": category, "category_source": source,
            "status": t.get("status", "booked"),
            "raw": json.dumps(obj, ensure_ascii=False),
            "labels": keep_labels.get(sig),
        })

    # Eksakt speil: fjern gamle rader for de arkiverte kontoene, sett inn på nytt.
    # (CSV-import har ingen arkiv-rader og røres derfor ikke.)
    if account_id:
        db.execute("DELETE FROM transactions WHERE account_id = ?", (account_id,))
    else:
        db.execute("DELETE FROM transactions WHERE account_id IN (SELECT DISTINCT account_id FROM raw_transactions)")
    db.upsert_many("transactions", records)
    apply_loan_transfers()   # lånetrekk → Overføring (etter at kategoriene er satt)
    return {"rebuilt": len(records)}
