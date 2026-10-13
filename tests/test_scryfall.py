"""Tests for the Scryfall client. All HTTP is mocked via httpx.MockTransport -
no real network calls happen in this suite.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.scryfall import ScryfallClient, ScryfallError, ScryfallOffline


def _json_response(status_code: int, payload: dict) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def _card(id="c1", name="Sol Ring", oracle_id="o1", **overrides) -> dict:
    data = {"object": "card", "id": id, "oracle_id": oracle_id, "name": name,
            "released_at": "2020-01-01"}
    data.update(overrides)
    return data


def make_client(handler) -> ScryfallClient:
    return ScryfallClient(transport=httpx.MockTransport(handler))


def test_get_card_success():
    def handler(request):
        assert request.url.path == "/cards/abc"
        return _json_response(200, _card(id="abc"))

    card = make_client(handler).get_card("abc")
    assert card["id"] == "abc"


def test_get_card_error_object_raises():
    def handler(request):
        return _json_response(404, {"object": "error", "details": "not found"})

    with pytest.raises(ScryfallError):
        make_client(handler).get_card("missing")


def test_search_by_name_resolves_via_fuzzy_then_oracle_id():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/cards/named":
            assert request.url.params["fuzzy"] == "lighming bolt"
            return _json_response(200, _card(name="Lightning Bolt", oracle_id="oracle-1"))
        assert request.url.path == "/cards/search"
        assert request.url.params["q"] == "oracleid:oracle-1"
        return _json_response(200, {
            "object": "list",
            "has_more": False,
            "data": [_card(id="p1", oracle_id="oracle-1"), _card(id="p2", oracle_id="oracle-1")],
        })

    results = make_client(handler).search_by_name("lighming bolt")
    assert [c["id"] for c in results] == ["p1", "p2"]
    assert len(calls) == 2


def test_search_by_name_falls_back_to_plain_search_when_fuzzy_misses():
    def handler(request):
        if request.url.path == "/cards/named":
            return _json_response(404, {"object": "error", "details": "no match"})
        assert request.url.params["q"] == "gibberish xyz"
        return _json_response(200, {"object": "list", "has_more": False, "data": []})

    assert make_client(handler).search_by_name("gibberish xyz") == []


def test_search_by_name_follows_pagination():
    pages = [
        {
            "object": "list", "has_more": True,
            "next_page": "https://api.scryfall.com/cards/search?page=2",
            "data": [_card(id="p1")],
        },
        {"object": "list", "has_more": False, "data": [_card(id="p2")]},
    ]
    seen = {"n": 0}

    def handler(request):
        if request.url.path == "/cards/named":
            return _json_response(200, _card(oracle_id="oracle-1"))
        page = pages[seen["n"]]
        seen["n"] += 1
        return _json_response(200, page)

    results = make_client(handler).search_by_name("sol ring")
    assert [c["id"] for c in results] == ["p1", "p2"]


def test_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr("app.scryfall.time.sleep", lambda s: None)
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] < 2:
            return httpx.Response(429, text="slow down")
        return _json_response(200, _card(id="abc"))

    card = make_client(handler).get_card("abc")
    assert card["id"] == "abc"
    assert attempts["n"] == 2


def test_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr("app.scryfall.time.sleep", lambda s: None)

    def handler(request):
        return httpx.Response(503, text="down")

    with pytest.raises(ScryfallError):
        make_client(handler).get_card("abc")


def test_connection_failure_raises_scryfall_offline(monkeypatch):
    """No network at all (DNS/connect failure) should be distinguishable from
    Scryfall responding with an error, so callers can prompt "connect to wifi"
    instead of a generic failure."""
    monkeypatch.setattr("app.scryfall.time.sleep", lambda s: None)

    def handler(request):
        raise httpx.ConnectError("no network", request=request)

    with pytest.raises(ScryfallOffline):
        make_client(handler).get_card("abc")


def test_scryfall_offline_is_a_scryfall_error():
    # so code that only handles the general case still catches this too
    assert issubclass(ScryfallOffline, ScryfallError)


def test_get_card_by_set_number_success():
    def handler(request):
        assert request.url.path == "/cards/cmr/472"
        return _json_response(200, _card(id="abc", set="cmr", collector_number="472"))

    card = make_client(handler).get_card_by_set_number("cmr", "472")
    assert card["id"] == "abc"


def test_get_card_by_set_number_lowercases_set_code():
    def handler(request):
        assert request.url.path == "/cards/cmr/472"
        return _json_response(200, _card(id="abc"))

    make_client(handler).get_card_by_set_number("CMR", "472")


def test_get_card_by_set_number_not_found_raises():
    def handler(request):
        return _json_response(404, {"object": "error", "details": "not found"})

    with pytest.raises(ScryfallError):
        make_client(handler).get_card_by_set_number("cmr", "999999")


def test_search_by_query_passes_query_through():
    def handler(request):
        assert request.url.path == "/cards/search"
        assert request.url.params["q"] == "set:cmr sol ring"
        return _json_response(200, {
            "object": "list", "has_more": False,
            "data": [_card(id="p1"), _card(id="p2")],
        })

    results = make_client(handler).search_by_query("set:cmr sol ring")
    assert [c["id"] for c in results] == ["p1", "p2"]


def test_get_cards_collection_chunks_at_75(monkeypatch):
    monkeypatch.setattr("app.scryfall.time.sleep", lambda s: None)
    batches = []

    def handler(request):
        ids = [item["id"] for item in json.loads(request.content)["identifiers"]]
        batches.append(ids)
        return _json_response(200, {
            "object": "list", "not_found": [],
            "data": [_card(id=i) for i in ids],
        })

    ids = [f"id-{i}" for i in range(150)]
    result = make_client(handler).get_cards_collection(ids)
    assert [len(b) for b in batches] == [75, 75]
    assert len(result) == 150
