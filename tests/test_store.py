"""Round-trip tests for the Phase 1 data layer. No network involved.

Run from the project root:

    python -m pytest
"""
from __future__ import annotations

import pytest

from app import store


def make_card(card_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", name="Sol Ring", **overrides):
    data = {
        "object": "card",
        "id": card_id,
        "oracle_id": "oid-" + card_id,
        "name": name,
        "set": "cmr",
        "set_name": "Commander Legends",
        "collector_number": "472",
        "rarity": "uncommon",
        "type_line": "Artifact",
        "mana_cost": "{1}",
        "oracle_text": "{T}: Add {C}{C}.",
        "lang": "en",
        "released_at": "2020-11-20",
        "image_uris": {"normal": "https://example.test/sol.jpg"},
        "finishes": ["nonfoil", "foil"],
        "prices": {"usd": "1.50", "usd_foil": "5.00", "usd_etched": None, "eur": "1.20"},
    }
    data.update(overrides)
    return data


def test_card_round_trip(db):
    store.upsert_card(make_card())
    got = store.get_card("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    assert got["name"] == "Sol Ring"
    assert got["set_code"] == "cmr"
    assert got["image_uri"] == "https://example.test/sol.jpg"
    assert got["fetched_at"]


def test_get_missing_card_returns_none(db):
    assert store.get_card("does-not-exist") is None


def test_upsert_card_is_idempotent_and_updates(db):
    store.upsert_card(make_card())
    store.upsert_card(make_card(name="Sol Ring (errata)"))
    got = store.get_card("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    assert got["name"] == "Sol Ring (errata)"


def test_prices_round_trip_and_age(db):
    card = make_card()
    store.upsert_card(card)
    store.upsert_prices(card)
    prices = store.get_prices(card["id"])
    assert prices["usd"] == 1.5
    assert prices["usd_foil"] == 5.0
    assert prices["usd_etched"] is None
    age = store.price_age_days(card["id"])
    assert age is not None and age < 1 / 24  # fresher than an hour


def test_price_age_is_none_without_a_price_row(db):
    card = make_card()
    store.upsert_card(card)
    assert store.price_age_days(card["id"]) is None


def test_add_to_collection_merges_on_conflict(db):
    card = make_card()
    store.upsert_card(card)
    store.add_to_collection(card["id"], quantity=2)
    row = store.add_to_collection(card["id"], quantity=3)
    assert row["quantity"] == 5


def test_add_to_collection_requires_a_cached_card(db):
    with pytest.raises(ValueError):
        store.add_to_collection("ghost-card", quantity=1)


def test_add_to_collection_rejects_bad_finish(db):
    card = make_card()
    store.upsert_card(card)
    with pytest.raises(ValueError):
        store.add_to_collection(card["id"], finish="shiny")


def test_list_collection_values_by_finish(db):
    card = make_card()
    store.upsert_card(card)
    store.upsert_prices(card)
    store.add_to_collection(card["id"], quantity=2, finish="nonfoil")  # 2 * 1.50
    store.add_to_collection(card["id"], quantity=1, finish="foil")     # 1 * 5.00
    result = store.list_collection()
    assert result["total"] == 8.0
    assert len(result["rows"]) == 2


def test_list_collection_skips_unknown_prices_in_total(db):
    card = make_card(prices={"usd": None, "usd_foil": None, "usd_etched": None, "eur": None})
    store.upsert_card(card)
    store.upsert_prices(card)
    store.add_to_collection(card["id"], quantity=3)
    result = store.list_collection()
    assert result["rows"][0]["line_value"] is None
    assert result["total"] == 0.0


def test_set_quantity_updates_and_zero_deletes(db):
    card = make_card()
    store.upsert_card(card)
    row = store.add_to_collection(card["id"], quantity=1)
    assert store.set_quantity(row["id"], 4)["quantity"] == 4
    assert store.set_quantity(row["id"], 0) is None
    assert store.list_collection()["rows"] == []


def test_remove_from_collection(db):
    card = make_card()
    store.upsert_card(card)
    row = store.add_to_collection(card["id"], quantity=1)
    store.remove_from_collection(row["id"])
    assert store.list_collection()["rows"] == []


def test_stale_card_ids(db):
    old = make_card(card_id="11111111-1111-4111-8111-111111111111")
    store.upsert_card(old)
    store.upsert_prices(old, updated_at="2000-01-01T00:00:00+00:00")

    fresh = make_card(card_id="22222222-2222-4222-8222-222222222222")
    store.upsert_card(fresh)
    store.upsert_prices(fresh)

    never = make_card(card_id="33333333-3333-4333-8333-333333333333")
    store.upsert_card(never)  # no price row at all

    stale = store.stale_card_ids(ttl_days=7)
    assert old["id"] in stale
    assert never["id"] in stale
    assert fresh["id"] not in stale


def test_stale_card_ids_only_collection(db):
    owned = make_card(card_id="11111111-1111-4111-8111-111111111111")
    store.upsert_card(owned)
    store.add_to_collection(owned["id"], quantity=1)

    unowned = make_card(card_id="22222222-2222-4222-8222-222222222222")
    store.upsert_card(unowned)

    stale = store.stale_card_ids(ttl_days=7, only_collection=True)
    assert stale == [owned["id"]]


def test_meta_round_trip_and_overwrite(db):
    assert store.get_meta("last_refresh_at") is None
    store.set_meta("last_refresh_at", "2026-01-01T00:00:00+00:00")
    assert store.get_meta("last_refresh_at") == "2026-01-01T00:00:00+00:00"
    store.set_meta("last_refresh_at", "2026-02-01T00:00:00+00:00")
    assert store.get_meta("last_refresh_at") == "2026-02-01T00:00:00+00:00"
