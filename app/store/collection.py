"""The `collection` table: cards the user owns.

Rows are unique per (card_id, finish, condition); adding a duplicate increases
the quantity. Valuation uses the price column matching each row's finish.
"""
from __future__ import annotations

from ..db import connect
from ._common import FINISHES, PRICE_FIELD, to_float, utcnow


def add_to_collection(
    card_id: str,
    quantity: int = 1,
    finish: str = "nonfoil",
    condition: str = "",
    notes: str = "",
) -> dict:
    """Add copies of a card. If the (card, finish, condition) row already exists,
    its quantity is increased. The card must already be in `cards`.
    """
    if finish not in FINISHES:
        raise ValueError(f"finish must be one of {FINISHES}, got {finish!r}")
    if quantity < 1:
        raise ValueError("quantity must be >= 1")
    condition = condition or ""

    with connect() as conn:
        if not conn.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone():
            raise ValueError(
                f"card {card_id!r} is not cached yet; add it to `cards` first"
            )
        conn.execute(
            "INSERT INTO collection "
            "(card_id, quantity, finish, condition, notes, added_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(card_id, finish, condition) DO UPDATE SET "
            "quantity = quantity + excluded.quantity, "
            "notes = CASE WHEN excluded.notes != '' "
            "             THEN excluded.notes ELSE collection.notes END",
            (card_id, quantity, finish, condition, notes or "", utcnow()),
        )
        row = conn.execute(
            "SELECT * FROM collection "
            "WHERE card_id = ? AND finish = ? AND condition = ?",
            (card_id, finish, condition),
        ).fetchone()
    return dict(row)


def set_quantity(collection_id: int, quantity: int) -> dict | None:
    """Set an exact quantity. A quantity below 1 deletes the row and returns None."""
    with connect() as conn:
        if quantity < 1:
            conn.execute("DELETE FROM collection WHERE id = ?", (collection_id,))
            return None
        conn.execute(
            "UPDATE collection SET quantity = ? WHERE id = ?",
            (quantity, collection_id),
        )
        row = conn.execute(
            "SELECT * FROM collection WHERE id = ?", (collection_id,)
        ).fetchone()
    return dict(row) if row else None


def remove_from_collection(collection_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM collection WHERE id = ?", (collection_id,))


def list_collection() -> dict:
    """Return every owned row joined with card + price data, plus a grand total.

    Each row gets `unit_price` and `line_value` (quantity * unit price) for the
    row's finish. Rows with no known price contribute None and are skipped in
    the total.
    """
    sql = """
        SELECT col.id, col.card_id, col.quantity, col.finish, col.condition,
               col.notes, col.added_at,
               c.name, c.set_code, c.set_name, c.collector_number, c.rarity,
               c.image_uri,
               p.usd, p.usd_foil, p.usd_etched
        FROM collection col
        JOIN cards c        ON c.id = col.card_id
        LEFT JOIN card_prices p ON p.card_id = col.card_id
        ORDER BY c.name, col.finish
    """
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql).fetchall()]

    total = 0.0
    for row in rows:
        unit = to_float(row.get(PRICE_FIELD[row["finish"]]))
        row["unit_price"] = unit
        row["line_value"] = round(unit * row["quantity"], 2) if unit is not None else None
        if row["line_value"] is not None:
            total += row["line_value"]
    return {"rows": rows, "total": round(total, 2)}
