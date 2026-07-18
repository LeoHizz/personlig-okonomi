"""SQLite-lag. Enkel og uten ORM — vi har full kontroll på skjemaet."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from . import config

_local = threading.local()


def _connect() -> sqlite3.Connection:
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
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
