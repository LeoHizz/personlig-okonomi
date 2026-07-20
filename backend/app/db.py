"""SQLite-lag. Enkel og uten ORM — vi har full kontroll på skjemaet."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from . import config


def account_number(iban: str | None, bban: str | None = None) -> str:
    """Normalisert kontonummer (kun sifre) fra IBAN eller BBAN – for matching/dedupe."""
    s = (iban or "").upper().replace(" ", "")
    if s.startswith("NO") and len(s) >= 15:
        return re.sub(r"\D", "", s[4:])
    return re.sub(r"\D", "", bban or "")

_local = threading.local()

# Demo-modus: bytter hele databasen til en egen demo.db uten å røre ekte data.
# Flagget er kun i minnet -> nullstilles ved omstart (ekte data igjen).
_DEMO = False


def demo_path() -> str:
    return str(Path(config.DB_PATH).parent / "demo.db")


def active_path() -> str:
    return demo_path() if _DEMO else config.DB_PATH


def set_demo(flag: bool) -> None:
    global _DEMO
    _DEMO = bool(flag)


def is_demo() -> bool:
    return _DEMO


def _connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_conn() -> sqlite3.Connection:
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = {}
        _local.conns = conns
    path = active_path()
    conn = conns.get(path)
    if conn is None:
        conn = _connect(path)
        conns[path] = conn
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS requisitions (
    id               TEXT PRIMARY KEY,
    institution_id   TEXT,
    institution_name TEXT,
    reference        TEXT,
    status           TEXT,
    link             TEXT,
    created_at       TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
    id               TEXT PRIMARY KEY,
    requisition_id   TEXT,
    institution_id   TEXT,
    institution_name TEXT,
    bank_code        TEXT,           -- kort etikett, f.eks. SPV / DNB / COOP
    iban             TEXT,
    bban             TEXT,           -- internt kontonummer (når IBAN mangler)
    provider_ref     TEXT,           -- bankens økt-ID (uid) for API-kall; id er stabil
    name             TEXT,           -- visningsnavn
    owner            TEXT,           -- hvem: Felles / Anna / Martin ...
    currency         TEXT,
    product          TEXT,
    status           TEXT,
    is_asset         INTEGER DEFAULT 1,   -- teller som formue i netto formue
    hidden           INTEGER DEFAULT 0,   -- skjul fra dashboard
    sort_order       INTEGER DEFAULT 100,
    created_at       TEXT,
    last_synced      TEXT
);

CREATE TABLE IF NOT EXISTS balances (
    account_id     TEXT,
    balance_type   TEXT,
    amount         REAL,
    currency       TEXT,
    reference_date TEXT,
    PRIMARY KEY (account_id, balance_type)
);

CREATE TABLE IF NOT EXISTS transactions (
    id              TEXT PRIMARY KEY,
    account_id      TEXT,
    booking_date    TEXT,
    value_date      TEXT,
    amount          REAL,
    currency        TEXT,
    counterparty    TEXT,
    remittance      TEXT,
    category        TEXT,
    category_source TEXT DEFAULT 'auto',  -- auto | manual
    status          TEXT,                 -- booked | pending
    raw             TEXT
);

CREATE INDEX IF NOT EXISTS idx_tx_account ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_tx_date    ON transactions(booking_date);

-- Daglige øyeblikksbilder av netto likviditet (kan ikke rekonstrueres ærlig
-- bakover, så vi lagrer faktiske målinger framover og bygger grafen fra dem).
CREATE TABLE IF NOT EXISTS liquidity_snapshots (
    date TEXT PRIMARY KEY,   -- YYYY-MM-DD
    cash REAL,
    debt REAL,
    net  REAL
);

-- KILDE-LAG: rått, urørt arkiv av bankens transaksjonsobjekter (verbatim).
-- Nøkkel = innholds-hash, så samme objekt lagres én gang (idempotent). Røres
-- ALDRI av tolkning – alt appen viser deriveres FRA denne.
CREATE TABLE IF NOT EXISTS raw_transactions (
    content_hash    TEXT PRIMARY KEY,
    account_id      TEXT,
    provider        TEXT,
    fetched_at      TEXT,
    entry_reference TEXT,
    booking_date    TEXT,
    amount          REAL,
    raw             TEXT NOT NULL        -- hele bankens JSON, verbatim
);
CREATE INDEX IF NOT EXISTS idx_raw_account ON raw_transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_raw_date    ON raw_transactions(booking_date);

-- Revisjonslogg for hvert synk-forsøk (slutt på stille svelging av feil).
CREATE TABLE IF NOT EXISTS sync_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   TEXT,
    started_at   TEXT,
    status       TEXT,        -- ok | error
    http_status  INTEGER,
    count        INTEGER,
    error_detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_sync_runs_acct ON sync_runs(account_id, started_at);
"""


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()


# --- settings (nøkkel/verdi med JSON) ---

def get_setting(key: str, default: Any = None) -> Any:
    row = get_conn().execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return default


def set_setting(key: str, value: Any) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )
    conn.commit()


# --- generiske hjelpere ---

def upsert(table: str, row: dict, conflict_col: str = "id") -> None:
    cols = list(row.keys())
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != conflict_col)
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT({conflict_col}) DO UPDATE SET {updates}"
    )
    conn = get_conn()
    conn.execute(sql, [row[c] for c in cols])
    conn.commit()


def query(sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
    return get_conn().execute(sql, tuple(params)).fetchall()


def execute(sql: str, params: Iterable = ()) -> None:
    conn = get_conn()
    conn.execute(sql, tuple(params))
    conn.commit()


def execute_rowcount(sql: str, params: Iterable = ()) -> int:
    """Som execute(), men returnerer antall berørte rader (f.eks. for INSERT OR IGNORE)."""
    conn = get_conn()
    cur = conn.execute(sql, tuple(params))
    conn.commit()
    return cur.rowcount


def insert_ignore_many(sql: str, rows: list) -> int:
    """Kjør mange INSERT (OR IGNORE) i ÉN transaksjon (ett commit). Returnerer
    antall faktisk innsatte rader (ignorerte teller ikke)."""
    conn = get_conn()
    before = conn.total_changes
    conn.executemany(sql, [tuple(r) for r in rows])
    conn.commit()
    return conn.total_changes - before
