"""CSV-import av transaksjoner fra norske nettbanker (DNB, Sparebanken, Coop m.fl.).

Formatene varierer, så vi auto-detekterer skilletegn og kolonner i stedet for
å kreve ett bestemt oppsett. Støtter:
  - skilletegn ; , eller tab
  - ett "Beløp"-felt (med fortegn), eller separate Inn/Ut-kolonner
  - norske tall (1 234,56 / 1.234,56 / -842,00) og datoer (dd.mm.åååå, åååå-mm-dd)
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime

from . import categorize, db, gocardless as gc

DATE_KEYS = ["bokført", "bokfort", "dato", "date", "transaksjonsdato", "rentedato"]
DESC_KEYS = ["tekst", "beskrivelse", "forklaring", "melding", "description", "details", "narrative"]
AMOUNT_KEYS = ["beløp", "belop", "amount", "sum"]
OUT_KEYS = ["ut av konto", "ut", "debet", "belastet", "uttak", "beløp ut", "belop ut"]
IN_KEYS = ["inn på konto", "inn", "kredit", "godskrevet", "innskudd", "beløp inn", "belop inn"]


def _norm(h: str) -> str:
    return (h or "").strip().strip('"').lower()


def _find(headers: list[str], keys: list[str]) -> int | None:
    for i, h in enumerate(headers):
        hn = _norm(h)
        for k in keys:
            if hn == k:
                return i
    # delvis treff som fallback
    for i, h in enumerate(headers):
        hn = _norm(h)
        for k in keys:
            if k in hn:
                return i
    return None


def parse_amount(s: str) -> float | None:
    if s is None:
        return None
    s = str(s).strip().replace(" ", "").replace(" ", "")
    if s in ("", "-", "0"):
        return 0.0 if s == "0" else None
    neg = s.startswith("-") or s.startswith("(")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    s = re.sub(r"[^0-9.]", "", s)
    if s == "":
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if neg else val


def parse_date(s: str) -> str | None:
    s = (s or "").strip().strip('"')
    if not s:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _sniff_delimiter(sample: str) -> str:
    counts = {d: sample.count(d) for d in (";", "\t", ",")}
    return max(counts, key=counts.get) if any(counts.values()) else ";"


def parse_csv(text: str) -> tuple[list[dict], list[str]]:
    """Returnerer (transaksjoner, advarsler)."""
    warnings: list[str] = []
    text = text.lstrip("﻿")
    first_line = text.splitlines()[0] if text.strip() else ""
    delim = _sniff_delimiter(first_line)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if len(rows) < 2:
        return [], ["Fant ingen datarader i filen."]

    headers = rows[0]
    di = _find(headers, DATE_KEYS)
    ai = _find(headers, AMOUNT_KEYS)
    oi = _find(headers, OUT_KEYS)
    ii = _find(headers, IN_KEYS)
    ti = _find(headers, DESC_KEYS)

    if di is None:
        return [], ["Fant ingen dato-kolonne. Sjekk at filen har en kolonneoverskrift."]
    if ai is None and oi is None and ii is None:
        return [], ["Fant ingen beløps-kolonne (Beløp, eller Inn/Ut)."]
    # «Beløp inn»/«Beløp ut» inneholder delstrengen «beløp», så AMOUNT-fallbacken
    # kan kuppe én av inn/ut-kolonnene og da forsvinner den andre (alle utgifter!).
    # Hvis beløps-feltet egentlig ER en inn/ut-kolonne, ignorer det og bruk inn/ut.
    if ai is not None and ai in (oi, ii):
        ai = None

    out = []
    for r in rows[1:]:
        def cell(idx):
            return r[idx] if idx is not None and idx < len(r) else ""

        date = parse_date(cell(di))
        if not date:
            continue

        if ai is not None:
            amount = parse_amount(cell(ai))
        elif ii is not None or oi is not None:
            inn = parse_amount(cell(ii)) or 0.0
            ut = parse_amount(cell(oi)) or 0.0
            # Ut oppgis ofte som positivt tall i egen kolonne -> gjør negativt
            amount = inn - abs(ut)
        else:
            continue
        if amount is None:
            continue

        desc = cell(ti).strip().strip('"')
        out.append(
            {
                "bookingDate": date,
                "amount": amount,
                "counterparty": desc,
                "remittance": desc,
            }
        )
    if not out:
        warnings.append("Ingen gyldige rader kunne tolkes.")
    return out, warnings


def ensure_import_account(name: str, bank_code: str, owner: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "import"
    acc_id = f"import:{slug}"
    existing = db.query("SELECT id FROM accounts WHERE id = ?", (acc_id,))
    if not existing:
        db.upsert(
            "accounts",
            {
                "id": acc_id,
                "requisition_id": "",
                "institution_id": "csv-import",
                "institution_name": "CSV-import",
                "bank_code": bank_code or "CSV",
                "iban": "",
                "name": name,
                "owner": owner or "Felles",
                "currency": "NOK",
                "product": "import",
                "status": "READY",
                "is_asset": 0,  # historikk uten saldo teller ikke som formue
                "hidden": 0,
                "created_at": gc.utc_now_iso(),
            },
        )
    return acc_id


def import_transactions(account_id: str, parsed: list[dict]) -> int:
    count = 0
    for t in parsed:
        amount = t["amount"]
        counterparty = t["counterparty"]
        remittance = t["remittance"]
        tx_id = "csv_" + hashlib.sha1(
            f"{account_id}|{t['bookingDate']}|{amount}|{remittance}".encode()
        ).hexdigest()[:20]

        existing = db.query(
            "SELECT category_source FROM transactions WHERE id = ?", (tx_id,)
        )
        if existing and existing[0]["category_source"] == "manual":
            continue  # ikke overskriv manuell kategori

        category = categorize.categorize(counterparty, remittance, amount)
        db.upsert(
            "transactions",
            {
                "id": tx_id,
                "account_id": account_id,
                "booking_date": t["bookingDate"],
                "value_date": t["bookingDate"],
                "amount": amount,
                "currency": "NOK",
                "counterparty": counterparty,
                "remittance": remittance,
                "category": category,
                "category_source": "auto",
                "status": "booked",
                "raw": "",
            },
        )
        count += 1
    return count
