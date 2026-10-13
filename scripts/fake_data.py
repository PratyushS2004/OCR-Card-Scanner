"""Populate data/cards.db with a handful of realistic cards so there is
something to look at before the Scryfall client exists.

Run from the project root:

    python -m scripts.fake_data

Card rows use the Scryfall card-object shape (same as the real API returns), so
they exercise the exact mapping code the live client will feed later. The UUIDs
here are fabricated - they are only sample data. This stands in for Scryfall as
the data source until the real client (Phase 2) exists.
"""
from __future__ import annotations

from app import config, store
from app.db import connect, init_db

# A price timestamp old enough that stale_card_ids(7) would flag it.
_OLD_TS = "2000-01-01T00:00:00+00:00"

SAMPLE_CARDS: list[dict] = [
    {
        "object": "card",
        "id": "11111111-1111-4111-8111-111111111111",
        "oracle_id": "aaaa1111-1111-4111-8111-111111111111",
        "name": "Sol Ring",
        "set": "cmr",
        "set_name": "Commander Legends",
        "collector_number": "472",
        "rarity": "uncommon",
        "type_line": "Artifact",
        "mana_cost": "{1}",
        "oracle_text": "{T}: Add {C}{C}.",
        "lang": "en",
        "released_at": "2020-11-20",
        "image_uris": {"normal": "https://cards.scryfall.io/normal/front/1/1/sol-ring.jpg"},
        "finishes": ["nonfoil", "foil"],
        "prices": {"usd": "1.29", "usd_foil": "6.50", "usd_etched": None, "eur": "1.10"},
    },
    {
        "object": "card",
        "id": "22222222-2222-4222-8222-222222222222",
        "oracle_id": "aaaa2222-2222-4222-8222-222222222222",
        "name": "Lightning Bolt",
        "set": "2x2",
        "set_name": "Double Masters 2022",
        "collector_number": "117",
        "rarity": "uncommon",
        "type_line": "Instant",
        "mana_cost": "{R}",
        "oracle_text": "Lightning Bolt deals 3 damage to any target.",
        "lang": "en",
        "released_at": "2022-07-08",
        "image_uris": {"normal": "https://cards.scryfall.io/normal/front/2/2/lightning-bolt.jpg"},
        "finishes": ["nonfoil", "foil"],
        "prices": {"usd": "1.10", "usd_foil": "3.20", "usd_etched": None, "eur": "0.95"},
    },
    {
        "object": "card",
        "id": "33333333-3333-4333-8333-333333333333",
        "oracle_id": "aaaa3333-3333-4333-8333-333333333333",
        "name": "Counterspell",
        "set": "mh2",
        "set_name": "Modern Horizons 2",
        "collector_number": "267",
        "rarity": "uncommon",
        "type_line": "Instant",
        "mana_cost": "{U}{U}",
        "oracle_text": "Counter target spell.",
        "lang": "en",
        "released_at": "2021-06-18",
        "image_uris": {"normal": "https://cards.scryfall.io/normal/front/3/3/counterspell.jpg"},
        "finishes": ["nonfoil", "foil"],
        "prices": {"usd": "0.85", "usd_foil": "2.40", "usd_etched": None, "eur": "0.70"},
    },
    {
        "object": "card",
        "id": "44444444-4444-4444-8444-444444444444",
        "oracle_id": "aaaa4444-4444-4444-8444-444444444444",
        "name": "Llanowar Elves",
        "set": "dom",
        "set_name": "Dominaria",
        "collector_number": "168",
        "rarity": "common",
        "type_line": "Creature — Elf Druid",
        "mana_cost": "{G}",
        "oracle_text": "{T}: Add {G}.",
        "lang": "en",
        "released_at": "2018-04-27",
        "image_uris": {"normal": "https://cards.scryfall.io/normal/front/4/4/llanowar-elves.jpg"},
        "finishes": ["nonfoil"],
        # No foil price on purpose - exercises the "price missing" path.
        "prices": {"usd": "0.20", "usd_foil": None, "usd_etched": None, "eur": "0.15"},
    },
    {
        "object": "card",
        "id": "55555555-5555-4555-8555-555555555555",
        "oracle_id": "aaaa5555-5555-4555-8555-555555555555",
        "name": "The One Ring",
        "set": "ltr",
        "set_name": "The Lord of the Rings: Tales of Middle-earth",
        "collector_number": "246",
        "rarity": "mythic",
        "type_line": "Legendary Artifact",
        "mana_cost": "{4}",
        "oracle_text": (
            "Indestructible\nWhen The One Ring enters the battlefield, if you cast "
            "it, you gain protection from everything until your next turn.\n"
            "At the beginning of your upkeep, you lose 1 life for each burden "
            "counter on The One Ring.\n{T}: Put a burden counter on The One Ring, "
            "then draw a card for each burden counter on it."
        ),
        "lang": "en",
        "released_at": "2023-06-23",
        "image_uris": {"normal": "https://cards.scryfall.io/normal/front/5/5/the-one-ring.jpg"},
        "finishes": ["nonfoil", "foil", "etched"],
        "prices": {"usd": "42.00", "usd_foil": "55.00", "usd_etched": "120.00", "eur": "38.00"},
    },
]

# (index into SAMPLE_CARDS, quantity, finish)
COLLECTION_ROWS = [
    (0, 2, "nonfoil"),   # 2x Sol Ring
    (1, 1, "foil"),      # 1x Lightning Bolt (foil)
    (3, 4, "nonfoil"),   # 4x Llanowar Elves
    (4, 1, "etched"),    # 1x The One Ring (etched)
]


def main() -> None:
    init_db()

    for card in SAMPLE_CARDS:
        store.upsert_card(card)
        # Give one card a deliberately stale price so refresh logic has a target.
        stale = card["id"] == SAMPLE_CARDS[2]["id"]
        store.upsert_prices(card, updated_at=_OLD_TS if stale else None)

    # Reset the collection so re-running the seed gives a stable result.
    with connect() as conn:
        conn.execute("DELETE FROM collection")
    for idx, qty, finish in COLLECTION_ROWS:
        store.add_to_collection(SAMPLE_CARDS[idx]["id"], quantity=qty, finish=finish)

    result = store.list_collection()
    print(f"Seeded {len(SAMPLE_CARDS)} cards into {config.DB_PATH}")
    print("\nCollection:")
    for row in result["rows"]:
        value = f"${row['line_value']:.2f}" if row["line_value"] is not None else "  n/a"
        print(f"  {row['quantity']:>2}x {row['name']:<16} {row['set_code']:<4} "
              f"{row['finish']:<8} {value:>8}")
    print(f"\nCollection value: ${result['total']:.2f}")

    stale_ids = store.stale_card_ids(ttl_days=7)
    print(f"Stale price rows (TTL 7d): {len(stale_ids)} -> {stale_ids}")


if __name__ == "__main__":
    main()
