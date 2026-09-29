"""Tests for app/service.py: the ranking layer, and the cache-aside read/write
layer (get_card, add_owned, refresh_prices). All Scryfall calls are
monkeypatched so nothing here touches the network; the cache-aside tests use
the `db` fixture (tests/conftest.py) for an isolated, throwaway SQLite file.
"""
from __future__ import annotations

import pytest

from app import service, store
from app.scryfall import ScryfallOffline


def _card(id, name, released_at="2020-01-01", **overrides):
    data = {"id": id, "name": name, "released_at": released_at, "prices": {"usd": "1.00"}}
    data.update(overrides)
    return data


def test_ties_break_by_most_recent_release(monkeypatch):
    # Same name (a real fuzzy match) -> identical similarity score -> recency decides.
    older = _card("old", "Sol Ring", released_at="2010-01-01")
    newer = _card("new", "Sol Ring", released_at="2023-01-01")
    monkeypatch.setattr(service.scryfall, "search_by_name", lambda text: [older, newer])

    results = service.search("sol ring")
    assert [c["id"] for c in results] == ["new", "old"]


def test_closer_name_match_ranks_first(monkeypatch):
    exact = _card("exact", "Lightning Bolt")
    unrelated = _card("far", "Lightning Strike")
    monkeypatch.setattr(service.scryfall, "search_by_name", lambda text: [unrelated, exact])

    results = service.search("Lightning Bolt")
    assert results[0]["id"] == "exact"


def test_respects_limit(monkeypatch):
    cards = [_card(str(i), "Sol Ring", released_at=f"20{i:02d}-01-01") for i in range(20)]
    monkeypatch.setattr(service.scryfall, "search_by_name", lambda text: cards)

    results = service.search("sol ring", limit=5)
    assert len(results) == 5


def test_does_not_pad_when_fewer_than_limit(monkeypatch):
    # Only 3 real printings exist - the result must not be padded up to `limit`.
    cards = [_card(str(i), "Aeve, Progenitor Ooze") for i in range(3)]
    monkeypatch.setattr(service.scryfall, "search_by_name", lambda text: cards)

    results = service.search("aeve", limit=10)
    assert len(results) == 3


def test_candidate_shape(monkeypatch):
    card = _card(
        "abc", "Sol Ring", set="cmr", set_name="Commander Legends",
        collector_number="472", rarity="uncommon",
        image_uris={"normal": "https://example.test/sol.jpg"},
    )
    monkeypatch.setattr(service.scryfall, "search_by_name", lambda text: [card])

    result = service.search("sol ring")[0]
    assert result == {
        "id": "abc",
        "name": "Sol Ring",
        "set_code": "cmr",
        "set_name": "Commander Legends",
        "collector_number": "472",
        "rarity": "uncommon",
        "released_at": "2020-01-01",
        "image_uri": "https://example.test/sol.jpg",
        "price_usd": "1.00",
    }


def test_empty_search_returns_empty_list(monkeypatch):
    monkeypatch.setattr(service.scryfall, "search_by_name", lambda text: [])
    assert service.search("gibberish") == []


def test_search_with_set_and_collector_number_resolves_exact_printing(monkeypatch):
    exact = _card("exact", "Sol Ring", set="cmr", collector_number="472")

    def fake_direct(set_code, collector_number):
        assert set_code == "cmr"
        assert collector_number == "472"
        return exact

    monkeypatch.setattr(service.scryfall, "get_card_by_set_number", fake_direct)
    monkeypatch.setattr(
        service.scryfall, "search_by_name",
        lambda text: (_ for _ in ()).throw(AssertionError("should not fall back")),
    )

    results = service.search("sol ring", set_code="cmr", collector_number="472")
    assert [c["id"] for c in results] == ["exact"]


def test_search_falls_back_to_name_when_set_number_not_found(monkeypatch):
    def fake_direct(set_code, collector_number):
        raise service.scryfall.ScryfallError("not found")

    monkeypatch.setattr(service.scryfall, "get_card_by_set_number", fake_direct)
    # collector number was misread, but the set code may still be good - the
    # fallback stays narrowed to that set rather than dropping it entirely.
    monkeypatch.setattr(
        service.scryfall, "search_by_query", lambda query: [_card("fallback", "Sol Ring")]
    )

    results = service.search("sol ring", set_code="cmr", collector_number="999999")
    assert [c["id"] for c in results] == ["fallback"]


def test_search_with_set_and_collector_number_propagates_offline(monkeypatch):
    def fake_direct(set_code, collector_number):
        raise ScryfallOffline("no network")

    monkeypatch.setattr(service.scryfall, "get_card_by_set_number", fake_direct)

    with pytest.raises(ScryfallOffline):
        service.search("sol ring", set_code="cmr", collector_number="472")


def test_search_with_only_set_code_uses_narrowed_query(monkeypatch):
    def fake_query(query):
        assert query == "set:cmr sol ring"
        return [_card("narrowed", "Sol Ring")]

    monkeypatch.setattr(service.scryfall, "search_by_query", fake_query)
    monkeypatch.setattr(
        service.scryfall, "search_by_name",
        lambda text: (_ for _ in ()).throw(AssertionError("should not fall back")),
    )

    results = service.search("sol ring", set_code="cmr")
    assert [c["id"] for c in results] == ["narrowed"]


def test_search_with_only_set_code_falls_back_when_narrowed_query_empty(monkeypatch):
    monkeypatch.setattr(service.scryfall, "search_by_query", lambda query: [])
    monkeypatch.setattr(
        service.scryfall, "search_by_name", lambda text: [_card("fallback", "Sol Ring")]
    )

    results = service.search("sol ring", set_code="cmr")
    assert [c["id"] for c in results] == ["fallback"]


# --------------------------------------------------------------------------- #
# get_card: the cache-aside read
# --------------------------------------------------------------------------- #

def test_get_card_miss_fetches_once_and_persists(db, monkeypatch):
    calls = {"n": 0}

    def fake_get_card(card_id):
        calls["n"] += 1
        return _card(card_id, "Sol Ring")

    monkeypatch.setattr(service.scryfall, "get_card", fake_get_card)

    result = service.get_card("abc")
    assert calls["n"] == 1
    assert result["name"] == "Sol Ring"
    assert result["usd"] == 1.0
    assert store.get_card("abc") is not None  # written back


def test_get_card_hit_within_ttl_makes_no_api_call(db, monkeypatch):
    calls = {"n": 0}

    def fake_get_card(card_id):
        calls["n"] += 1
        return _card(card_id, "Sol Ring")

    monkeypatch.setattr(service.scryfall, "get_card", fake_get_card)

    service.get_card("abc")           # miss -> 1 call
    result = service.get_card("abc")  # hit, price still fresh -> no more calls
    assert calls["n"] == 1
    assert result["name"] == "Sol Ring"


def test_get_card_refetches_only_the_price_when_stale(db, monkeypatch):
    calls = {"n": 0}

    def fake_get_card(card_id):
        calls["n"] += 1
        return _card(card_id, "Sol Ring", prices={"usd": "2.00"})

    monkeypatch.setattr(service.scryfall, "get_card", fake_get_card)

    service.get_card("abc")  # miss -> 1 call, card + price cached
    store.upsert_prices(
        _card("abc", "Sol Ring", prices={"usd": "1.00"}),
        updated_at="2000-01-01T00:00:00+00:00",
    )

    result = service.get_card("abc")  # price is stale -> 1 more call
    assert calls["n"] == 2
    assert result["usd"] == 2.0


def test_get_card_serves_stale_price_when_offline(db, monkeypatch):
    """A card we already have is more useful than an error - if refreshing a
    stale price fails because we're offline, fall back to what's cached."""
    monkeypatch.setattr(service.scryfall, "get_card", lambda cid: _card(cid, "Sol Ring"))
    service.get_card("abc")  # miss -> cached with usd 1.00 (see _card default)
    store.upsert_prices(
        _card("abc", "Sol Ring", prices={"usd": "1.00"}),
        updated_at="2000-01-01T00:00:00+00:00",
    )

    def offline(card_id):
        raise ScryfallOffline("no network")

    monkeypatch.setattr(service.scryfall, "get_card", offline)

    result = service.get_card("abc")  # stale + offline -> no exception, stale data served
    assert result["name"] == "Sol Ring"
    assert result["usd"] == 1.0


def test_get_card_miss_while_offline_raises(db, monkeypatch):
    """A cache MISS with no network has nothing to fall back to - this must
    raise so the (future) UI can prompt the user to connect."""

    def offline(card_id):
        raise ScryfallOffline("no network")

    monkeypatch.setattr(service.scryfall, "get_card", offline)

    with pytest.raises(ScryfallOffline):
        service.get_card("never-cached")


# --------------------------------------------------------------------------- #
# add_owned
# --------------------------------------------------------------------------- #

def test_add_owned_caches_the_card_then_records_ownership(db, monkeypatch):
    monkeypatch.setattr(service.scryfall, "get_card", lambda cid: _card(cid, "Sol Ring"))

    row = service.add_owned("abc", quantity=2, finish="foil")
    assert row["quantity"] == 2
    assert row["finish"] == "foil"
    assert store.get_card("abc") is not None


# --------------------------------------------------------------------------- #
# refresh_prices
# --------------------------------------------------------------------------- #

def test_refresh_prices_only_touches_stale_owned_cards(db, monkeypatch):
    old_ts = "2000-01-01T00:00:00+00:00"
    for cid in ("a", "b", "c"):
        store.upsert_card(_card(cid, f"Card {cid}"))
    store.upsert_prices(_card("a", "Card a", prices={"usd": "1.00"}), updated_at=old_ts)
    store.upsert_prices(_card("b", "Card b", prices={"usd": "1.00"}))               # fresh
    store.upsert_prices(_card("c", "Card c", prices={"usd": "1.00"}), updated_at=old_ts)
    store.add_to_collection("a", quantity=1)
    store.add_to_collection("b", quantity=1)
    # "c" is cached but not owned - refresh_prices must leave it alone.

    seen_ids = []

    def fake_collection(ids):
        seen_ids.extend(ids)
        return [_card(cid, f"Card {cid}", prices={"usd": "9.99"}) for cid in ids]

    monkeypatch.setattr(service.scryfall, "get_cards_collection", fake_collection)

    refreshed = service.refresh_prices()
    assert seen_ids == ["a"]
    assert refreshed == 1
    assert store.get_prices("a")["usd"] == 9.99
    assert store.get_prices("c")["usd"] == 1.0  # untouched


def test_refresh_prices_does_nothing_when_no_stale_owned_cards(db, monkeypatch):
    def fake_collection(ids):
        raise AssertionError("should not be called when nothing is stale")

    monkeypatch.setattr(service.scryfall, "get_cards_collection", fake_collection)
    assert service.refresh_prices() == 0


def test_refresh_prices_records_last_refresh_time(db, monkeypatch):
    monkeypatch.setattr(service.scryfall, "get_cards_collection", lambda ids: [])
    assert store.get_meta("last_refresh_at") is None
    service.refresh_prices()
    assert store.get_meta("last_refresh_at") is not None


def test_refresh_prices_does_not_record_time_when_offline(db, monkeypatch):
    old_ts = "2000-01-01T00:00:00+00:00"
    store.upsert_card(_card("a", "Card a"))
    store.upsert_prices(_card("a", "Card a"), updated_at=old_ts)
    store.add_to_collection("a", quantity=1)

    def offline(ids):
        raise ScryfallOffline("no network")

    monkeypatch.setattr(service.scryfall, "get_cards_collection", offline)
    with pytest.raises(ScryfallOffline):
        service.refresh_prices()
    assert store.get_meta("last_refresh_at") is None
