"""The `card_prices` table: mutable, refreshed on a TTL.

`prices_updated_at` is when *this app* last refreshed the row - that is what the
freshness check (and the weekly refresh job) look at, not any Scryfall timestamp.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..db import connect
from ._common import parse_ts, to_float, utcnow


def upsert_prices(data: dict, updated_at: str | None = None) -> None:
    """Store the price block from a Scryfall card dict.

    `updated_at` defaults to now; pass an explicit value for seeding or tests.
    """
    prices = data.get("prices") or {}
    row = {
        "card_id": data["id"],
        "usd": to_float(prices.get("usd")),
        "usd_foil": to_float(prices.get("usd_foil")),
        "usd_etched": to_float(prices.get("usd_etched")),
        "eur": to_float(prices.get("eur")),
        "prices_updated_at": updated_at or utcnow(),
    }
    sql = (
        "INSERT INTO card_prices "
        "(card_id, usd, usd_foil, usd_etched, eur, prices_updated_at) "
        "VALUES (:card_id, :usd, :usd_foil, :usd_etched, :eur, :prices_updated_at) "
        "ON CONFLICT(card_id) DO UPDATE SET "
        "usd=excluded.usd, usd_foil=excluded.usd_foil, usd_etched=excluded.usd_etched, "
        "eur=excluded.eur, prices_updated_at=excluded.prices_updated_at"
    )
    with connect() as conn:
        conn.execute(sql, row)


def get_prices(card_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM card_prices WHERE card_id = ?", (card_id,)
        ).fetchone()
    return dict(row) if row else None


def price_age_days(card_id: str) -> float | None:
    """Age of the stored price in days, or None if there is no price row."""
    prices = get_prices(card_id)
    if not prices or not prices.get("prices_updated_at"):
        return None
    delta = datetime.now(timezone.utc) - parse_ts(prices["prices_updated_at"])
    return delta.total_seconds() / 86_400.0


def stale_card_ids(ttl_days: float, only_collection: bool = False) -> list[str]:
    """Cards whose price is missing or older than ttl_days.

    Set `only_collection=True` to limit the result to cards you actually own -
    that is what the weekly refresh job will use.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).isoformat()
    sql = "SELECT c.id FROM cards c LEFT JOIN card_prices p ON p.card_id = c.id "
    if only_collection:
        sql += "JOIN collection col ON col.card_id = c.id "
    sql += "WHERE p.card_id IS NULL OR p.prices_updated_at < ? GROUP BY c.id"
    with connect() as conn:
        rows = conn.execute(sql, (cutoff,)).fetchall()
    return [r["id"] for r in rows]
