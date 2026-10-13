"""Small helpers shared by the store submodules. No SQL here."""
from __future__ import annotations

from datetime import datetime, timezone

# Card finishes Scryfall reports, and the price column each one is valued against.
FINISHES = ("nonfoil", "foil", "etched")
PRICE_FIELD = {"nonfoil": "usd", "foil": "usd_foil", "etched": "usd_etched"}


def utcnow() -> str:
    """Current UTC time as an ISO-8601 string (what every *_at column stores)."""
    return datetime.now(timezone.utc).isoformat()


def to_float(value) -> float | None:
    """Coerce a Scryfall price string (or None) to a float, or None if unusable."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_ts(text: str) -> datetime:
    """Parse an ISO timestamp, assuming UTC when no offset is present."""
    ts = datetime.fromisoformat(text)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
