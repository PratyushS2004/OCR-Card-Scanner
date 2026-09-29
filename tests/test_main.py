"""Route tests for app/main.py, using FastAPI's TestClient against the
throwaway `db` fixture. Scryfall is monkeypatched, same pattern as
test_service.py - nothing here touches the network.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import service
from app.main import app
from app.scryfall import ScryfallOffline


@pytest.fixture()
def client(db):
    with TestClient(app) as c:
        yield c


def _card(id, name, released_at="2020-01-01", **overrides):
    data = {
        "id": id,
        "name": name,
        "set": "cmr",
        "set_name": "Commander Legends",
        "collector_number": "472",
        "rarity": "uncommon",
        "released_at": released_at,
        "prices": {"usd": "1.00"},
    }
    data.update(overrides)
    return data


def test_index_loads(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Search" in resp.text


def test_search_renders_results(client, monkeypatch):
    monkeypatch.setattr(
        service.scryfall, "search_by_name", lambda text: [_card("abc", "Sol Ring")]
    )
    resp = client.get("/search", params={"q": "sol ring"})
    assert resp.status_code == 200
    assert "Sol Ring" in resp.text


def test_search_hx_request_returns_fragment_only(client, monkeypatch):
    monkeypatch.setattr(
        service.scryfall, "search_by_name", lambda text: [_card("abc", "Sol Ring")]
    )
    resp = client.get("/search", params={"q": "sol ring"}, headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "Sol Ring" in resp.text
    assert "<nav" not in resp.text  # fragment, not the full page shell


def test_search_offline_shows_notice(client, monkeypatch):
    def offline(text):
        raise ScryfallOffline("no network")

    monkeypatch.setattr(service.scryfall, "search_by_name", offline)
    resp = client.get("/search", params={"q": "sol ring"})
    assert resp.status_code == 200
    assert "offline" in resp.text.lower()


def test_search_no_query_shows_no_results(client):
    resp = client.get("/search", params={"q": ""})
    assert resp.status_code == 200


def test_card_detail_renders(client, monkeypatch):
    monkeypatch.setattr(service.scryfall, "get_card", lambda cid: _card(cid, "Sol Ring"))
    resp = client.get("/cards/abc")
    assert resp.status_code == 200
    assert "Sol Ring" in resp.text


def test_card_detail_offline_miss_shows_notice(client, monkeypatch):
    def offline(cid):
        raise ScryfallOffline("no network")

    monkeypatch.setattr(service.scryfall, "get_card", offline)
    resp = client.get("/cards/never-cached")
    assert resp.status_code == 200
    assert "offline" in resp.text.lower()


def test_add_to_collection_and_view(client, monkeypatch):
    monkeypatch.setattr(service.scryfall, "get_card", lambda cid: _card(cid, "Sol Ring"))

    resp = client.post(
        "/collection",
        data={"card_id": "abc", "finish": "nonfoil", "quantity": "2", "condition": "NM"},
    )
    assert resp.status_code == 200  # TestClient follows the 303 redirect
    assert "Sol Ring" in resp.text
    assert "NM" in resp.text


def test_update_quantity_and_delete(client, monkeypatch):
    monkeypatch.setattr(service.scryfall, "get_card", lambda cid: _card(cid, "Sol Ring"))
    row = service.add_owned("abc", quantity=1)

    resp = client.post(f"/collection/{row['id']}", data={"quantity": "5"})
    assert resp.status_code == 200
    assert "Sol Ring" in resp.text

    resp = client.post(f"/collection/{row['id']}/delete")
    assert resp.status_code == 200
    assert "No cards in your collection yet." in resp.text


def test_refresh_prices_redirects_with_count(client, monkeypatch):
    monkeypatch.setattr(service.scryfall, "get_cards_collection", lambda ids: [])
    resp = client.post("/refresh-prices")
    assert resp.status_code == 200
    assert "Refreshed 0 prices." in resp.text
    assert "Last refreshed" in resp.text


def test_refresh_prices_hx_request_returns_fragment(client, monkeypatch):
    monkeypatch.setattr(service.scryfall, "get_cards_collection", lambda ids: [])
    resp = client.post("/refresh-prices", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "Refreshed 0 prices." in resp.text
    assert "<nav" not in resp.text  # fragment, not the full page shell


def test_collection_stats_header(client, monkeypatch):
    monkeypatch.setattr(service.scryfall, "get_card", lambda cid: _card(cid, "Sol Ring"))
    service.add_owned("abc", quantity=3)

    resp = client.get("/collection")
    assert resp.status_code == 200
    assert "3" in resp.text  # total copies
    assert "$3.00" in resp.text  # 3 * $1.00 from _card()'s default price


def test_quantity_stepper_hx_request_swaps_fragment_and_updates_total(client, monkeypatch):
    monkeypatch.setattr(service.scryfall, "get_card", lambda cid: _card(cid, "Sol Ring"))
    row = service.add_owned("abc", quantity=1)

    resp = client.post(
        f"/collection/{row['id']}", data={"quantity": "3"}, headers={"HX-Request": "true"}
    )
    assert resp.status_code == 200
    assert "<nav" not in resp.text
    assert "$3.00" in resp.text  # value updated in the same swapped fragment


def test_quantity_stepper_to_zero_removes_row(client, monkeypatch):
    monkeypatch.setattr(service.scryfall, "get_card", lambda cid: _card(cid, "Sol Ring"))
    row = service.add_owned("abc", quantity=1)

    resp = client.post(
        f"/collection/{row['id']}", data={"quantity": "0"}, headers={"HX-Request": "true"}
    )
    assert resp.status_code == 200
    assert "No cards in your collection yet." in resp.text


def test_delete_hx_request_returns_updated_fragment(client, monkeypatch):
    monkeypatch.setattr(service.scryfall, "get_card", lambda cid: _card(cid, "Sol Ring"))
    row = service.add_owned("abc", quantity=1)

    resp = client.post(f"/collection/{row['id']}/delete", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "<nav" not in resp.text
    assert "No cards in your collection yet." in resp.text


def test_api_search_returns_json(client, monkeypatch):
    monkeypatch.setattr(
        service.scryfall, "search_by_name", lambda text: [_card("abc", "Sol Ring")]
    )
    resp = client.get("/api/search", params={"q": "sol ring"})
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["id"] == "abc"


def test_api_search_offline_returns_503(client, monkeypatch):
    def offline(text):
        raise ScryfallOffline("no network")

    monkeypatch.setattr(service.scryfall, "search_by_name", offline)
    resp = client.get("/api/search", params={"q": "sol ring"})
    assert resp.status_code == 503


def test_api_search_passes_set_and_collector_number(client, monkeypatch):
    def fake_direct(set_code, collector_number):
        assert set_code == "cmr"
        assert collector_number == "472"
        return _card("exact", "Sol Ring")

    monkeypatch.setattr(service.scryfall, "get_card_by_set_number", fake_direct)
    resp = client.get(
        "/api/search",
        params={"q": "sol ring", "set_code": "cmr", "collector_number": "472"},
    )
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == "exact"
