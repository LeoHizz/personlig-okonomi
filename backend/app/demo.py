"""Genererer realistiske demo-tall for å vise appen fram uten å avsløre ekte økonomi.
Kjøres kun mot demo.db (se db.set_demo)."""
from __future__ import annotations

from datetime import datetime, timezone

from . import categorize, db


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _month(offset: int) -> str:
    now = datetime.now()
    y, m = now.year, now.month - offset
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


ACCOUNTS = [
    ("demo-a1", "Brukskonto", "DNB", "Frode", 1, 52340),
    ("demo-a2", "Brukskonto", "SPV", "Eva", 1, 38120),
    ("demo-a3", "Felleskonto", "DNB", "Felles", 1, 19875),
    ("demo-card", "Coop Mastercard", "COOP", "Felles", 0, None),
]

# (kontonr-idx, dag, beløp, motpart) – kategori settes av reglene.
TX = [
    # inntekt
    (0, "15", 42500, "Lønn Acme AS"),
    (1, "15", 31800, "Lønn Bergen Kommune"),
    (2, "20", 3200, "Barnetrygd NAV"),
    # bolig / faste
    (2, "01", -18600, "Boliglån DNB avdrag"),
    (2, "05", -1450, "BIR renovasjon"),
    (2, "05", -890, "Vann og avløp"),
    (0, "06", -1290, "Fjordkraft strøm"),
    (0, "12", -199, "Netflix"),
    (1, "12", -129, "Spotify"),
    (0, "12", -449, "Telenor mobil"),
    (2, "10", -3400, "Bergen Barnehage"),
    (0, "08", -720, "Gjensidige forsikring"),
    # dagligvarer (på kortet)
    (3, "03", -842, "Rema 1000 Laksevåg"),
    (3, "07", -1235, "Kiwi Danmarksplass"),
    (3, "11", -643, "Meny Åsane"),
    (3, "14", -389, "Coop Extra Loddefjord"),
    (3, "19", -1120, "Rema 1000 Laksevåg"),
    (3, "24", -567, "Bunnpris Sentrum"),
    # transport
    (0, "09", -689, "Circle K Danmarksplass"),
    (0, "22", -540, "Vy Bergen"),
    # restaurant
    (0, "17", -456, "Egon Bergen"),
    (1, "21", -289, "Espresso House"),
    # fritid / klær / helse / hus
    (1, "13", -899, "XXL Sport Lagunen"),
    (1, "16", -650, "H&M Bergen Storsenter"),
    (0, "18", -410, "Vitusapotek"),
    (2, "23", -1290, "Jula Åsane"),
    # gebyr
    (3, "26", -45, "Fakturagebyr Kredittbanken"),
    # hytte
    (0, "04", -980, "Hytteforeningen Voss strøm"),
    (3, "20", -512, "Coop Prix Voss"),
    # kortavregning (overføring, ikke utgift)
    (0, "27", -4800, "Kredittbanken ASA nedbetaling"),
]


def seed_if_empty() -> None:
    """Fyller demo.db med data hvis den er tom. Forutsetter at db er i demo-modus."""
    if db.query("SELECT COUNT(*) AS n FROM accounts")[0]["n"] > 0:
        return

    for aid, name, code, owner, is_asset, bal in ACCOUNTS:
        db.upsert("accounts", {
            "id": aid, "requisition_id": "", "institution_id": "demo" if is_asset else "csv-import",
            "institution_name": "Demo-bank", "bank_code": code, "iban": "NO" + aid[-4:] * 3,
            "name": name, "owner": owner, "currency": "NOK", "product": "demo",
            "status": "READY", "is_asset": is_asset, "hidden": 0, "created_at": _now_iso(),
        })
        if bal is not None:
            db.execute(
                "INSERT OR REPLACE INTO balances(account_id, balance_type, amount, currency, reference_date) "
                "VALUES(?,?,?,?,?)", (aid, "closing", bal, "NOK", _month(0) + "-27"))

    n = 0
    for offset in range(4):  # siste 4 måneder
        m = _month(offset)
        for accidx, day, amt, cp in TX:
            aid = ACCOUNTS[accidx][0]
            cat = categorize.categorize(cp, "", amt)
            n += 1
            db.upsert("transactions", {
                "id": f"demo-{offset}-{n}", "account_id": aid, "booking_date": f"{m}-{day}",
                "value_date": f"{m}-{day}", "amount": amt, "currency": "NOK",
                "counterparty": cp, "remittance": "", "category": cat,
                "category_source": "auto", "status": "booked", "raw": "",
            })

    db.set_setting("household_name", "Familien Eksempel")
    db.set_setting("savings_goal_pct", 20)
    db.set_setting("budgets", {
        "Dagligvarer": 12000, "Transport": 2500, "Restaurant og kafé": 1500,
        "Fritid": 2000, "Strøm": 1500, "Abonnementer": 900, "Klær og sko": 1500,
    })
    db.set_setting("manual_assets", [{"name": "Bolig", "value": 5200000, "tag": "BOLIG", "owner": "Felles"}])
    db.set_setting("manual_liabilities", [{
        "name": "Boliglån", "tag": "DNB", "rate": "4,9", "auto": True,
        "start_balance": 3400000, "monthly_payment": 12000, "start_date": _month(6),
    }])
    db.set_setting("label_rules", [
        {"pattern": "hytteforeningen voss", "label": "Hytte"},
        {"pattern": "coop prix voss", "label": "Hytte"},
    ])
