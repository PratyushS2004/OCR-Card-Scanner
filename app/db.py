"""SQLite connection helpers and schema setup.

No network access lives here - this module only knows about the local file.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id               TEXT PRIMARY KEY,          -- Scryfall card UUID (one row per printing)
    oracle_id        TEXT,                      -- groups every printing of the same card
    name             TEXT NOT NULL,
    set_code         TEXT,
    set_name         TEXT,
    collector_number TEXT,
    rarity           TEXT,
    type_line        TEXT,
    mana_cost        TEXT,
    oracle_text      TEXT,
    image_uri        TEXT,
    lang             TEXT NOT NULL DEFAULT 'en',
    released_at      TEXT,                      -- ISO date, handy for ranking later
    raw_json         TEXT NOT NULL,             -- full payload for anything not columnised
    fetched_at       TEXT NOT NULL              -- when we first cached this card
);
CREATE INDEX IF NOT EXISTS idx_cards_oracle_id ON cards(oracle_id);
CREATE INDEX IF NOT EXISTS idx_cards_name      ON cards(name);

CREATE TABLE IF NOT EXISTS card_prices (
    card_id           TEXT PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
    usd               REAL,
    usd_foil          REAL,
    usd_etched        REAL,
    eur               REAL,
    prices_updated_at TEXT NOT NULL             -- when WE last refreshed; the TTL checks this
);

CREATE TABLE IF NOT EXISTS collection (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id    TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    quantity   INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 1),
    finish     TEXT NOT NULL DEFAULT 'nonfoil', -- nonfoil | foil | etched
    condition  TEXT NOT NULL DEFAULT '',        -- '' when unspecified (keeps UNIQUE working)
    notes      TEXT NOT NULL DEFAULT '',
    added_at   TEXT NOT NULL,
    UNIQUE (card_id, finish, condition)
);
CREATE INDEX IF NOT EXISTS idx_collection_card_id ON collection(card_id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def get_conn() -> sqlite3.Connection:
    """Open a connection with row access by name and foreign keys enforced."""
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def connect():
    """Yield a connection, commit on success, roll back on error, always close."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables and indexes if they do not exist yet. Safe to call repeatedly."""
    with connect() as conn:
        conn.executescript(SCHEMA)
