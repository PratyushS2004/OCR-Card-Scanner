"""The `cards` table: one row per printing, static data, effectively write-once.

`upsert_card` takes a Scryfall card object so the Phase 3 service can pass an API
response straight through.
"""
from __future__ import annotations

import json

from ..db import connect
from ._common import utcnow


def _row_from_scryfall(data: dict) -> dict:
    """Map a Scryfall card object onto our `cards` columns."""
    image = None
    if isinstance(data.get("image_uris"), dict):
        uris = data["image_uris"]
        image = uris.get("normal") or uris.get("large") or uris.get("small")
    elif isinstance(data.get("card_faces"), list) and data["card_faces"]:
        face = data["card_faces"][0]
        if isinstance(face.get("image_uris"), dict):
            image = face["image_uris"].get("normal")

    return {
        "id": data["id"],
        "oracle_id": data.get("oracle_id"),
        "name": data.get("name"),
        "set_code": data.get("set"),
        "set_name": data.get("set_name"),
        "collector_number": data.get("collector_number"),
        "rarity": data.get("rarity"),
        "type_line": data.get("type_line"),
        "mana_cost": data.get("mana_cost"),
        "oracle_text": data.get("oracle_text"),
        "image_uri": image,
        "lang": data.get("lang", "en"),
        "released_at": data.get("released_at"),
        "raw_json": json.dumps(data, separators=(",", ":")),
        "fetched_at": utcnow(),
    }


def upsert_card(data: dict) -> None:
    """Insert a card, or refresh its static columns if it is already cached.

    `fetched_at` keeps its original value on update so it records first-seen time.
    """
    row = _row_from_scryfall(data)
    cols = list(row.keys())
    placeholders = ",".join(f":{c}" for c in cols)
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("id", "fetched_at"))
    sql = (
        f"INSERT INTO cards ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}"
    )
    with connect() as conn:
        conn.execute(sql, row)


def _derived_fields(raw_json: str) -> dict:
    """Pull display fields that aren't worth a schema column out of the raw
    Scryfall payload we already store - power/toughness/loyalty, artist,
    and the card's Scryfall page. Falls back to the first face for
    double-faced cards, same as `_row_from_scryfall` does for image_uri.
    """
    try:
        raw = json.loads(raw_json)
    except (TypeError, ValueError):
        raw = {}
    face = raw
    if not any(k in raw for k in ("power", "toughness", "loyalty")) and raw.get("card_faces"):
        face = raw["card_faces"][0]

    return {
        "power": face.get("power"),
        "toughness": face.get("toughness"),
        "loyalty": face.get("loyalty"),
        "artist": raw.get("artist") or face.get("artist"),
        "scryfall_uri": raw.get("scryfall_uri"),
    }


def get_card(card_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
    if row is None:
        return None
    card = dict(row)
    card.update(_derived_fields(card["raw_json"]))
    return card
