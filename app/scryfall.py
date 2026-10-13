"""Scryfall API client - the only module that makes outbound HTTP calls.

Every function returns plain dicts shaped like Scryfall's card objects. This
module never touches the database; app/service.py (Phase 3) decides when to
call it at all (cache miss / stale price) and writes the result back.
"""
from __future__ import annotations

import time

import httpx

from . import config

RETRYABLE_STATUS = {429, 503}
MAX_RETRIES = 3
RETRY_BACKOFF = (0.5, 1.0, 2.0)  # seconds, one per retry attempt
MAX_SEARCH_PAGES = 25            # safety cap so a runaway query can't loop forever
COLLECTION_BATCH_SIZE = 75       # Scryfall's hard limit per /cards/collection call


class ScryfallError(Exception):
    """Raised when a Scryfall call fails after retries, or returns an error object."""


class ScryfallOffline(ScryfallError):
    """Raised when Scryfall couldn't be reached at all (DNS/connection failure),
    as opposed to Scryfall responding with an error. Callers can catch this
    specifically to distinguish "we're offline" from "Scryfall rejected this" -
    e.g. app/service.py falls back to a stale cached price instead of failing
    outright, and a future UI can prompt "connect to wifi" instead of a
    generic error page.
    """


class ScryfallClient:
    """Thin wrapper around httpx with Scryfall's rate-limit and retry rules built in.

    Pass a custom `transport` (e.g. httpx.MockTransport) in tests to avoid the
    network entirely; production code uses the module-level functions below,
    which share one lazily-created client.
    """

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(
            base_url=config.SCRYFALL_BASE,
            headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"},
            transport=transport,
            timeout=10.0,
        )
        self._last_request = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ScryfallClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- request plumbing: rate limit + retry -------------------------------

    def _throttle(self) -> None:
        wait = config.SCRYFALL_MIN_INTERVAL - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        last_exc: Exception | None = None
        offline = False  # tracks whether the *last* failed attempt was a connection failure
        for attempt in range(MAX_RETRIES + 1):
            self._throttle()
            try:
                resp = self._http.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                last_exc = exc
                offline = True
            else:
                self._last_request = time.monotonic()
                if resp.status_code not in RETRYABLE_STATUS:
                    return resp
                last_exc = ScryfallError(f"{method} {url} -> HTTP {resp.status_code}")
                offline = False
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF[attempt])
        if offline:
            raise ScryfallOffline(f"{method} {url}: could not reach Scryfall") from last_exc
        raise ScryfallError(f"{method} {url} failed after {MAX_RETRIES} retries") from last_exc

    # -- public API ----------------------------------------------------------

    def get_card(self, card_id: str) -> dict:
        """Fetch one card by its Scryfall id. Used on a cache miss and to refresh a price."""
        resp = self._request("GET", f"/cards/{card_id}")
        data = resp.json()
        if resp.status_code != 200 or data.get("object") == "error":
            raise ScryfallError(f"get_card({card_id!r}): {data.get('details', resp.text)}")
        return data

    def search_by_name(self, text: str, limit: int = 175) -> list[dict]:
        """Fuzzy-resolve `text` to a card, then return every printing of it.

        Falls back to a plain name search if the fuzzy lookup can't resolve
        (e.g. OCR text too garbled). May return an empty list if Scryfall has
        no match at all - callers should treat that as "no candidates", not
        an error.
        """
        oracle_id = self._fuzzy_oracle_id(text)
        query = f"oracleid:{oracle_id}" if oracle_id else text
        return self._search_all(query, limit)

    def _fuzzy_oracle_id(self, text: str) -> str | None:
        resp = self._request("GET", "/cards/named", params={"fuzzy": text})
        data = resp.json()
        if resp.status_code == 200 and data.get("object") != "error":
            return data.get("oracle_id")
        return None  # not found / too ambiguous -> caller falls back to a plain search

    def _search_all(self, query: str, limit: int) -> list[dict]:
        results: list[dict] = []
        url: str | None = "/cards/search"
        params = {"q": query, "unique": "prints", "order": "released"}
        page = 0
        while url and page < MAX_SEARCH_PAGES and len(results) < limit:
            resp = self._request("GET", url, params=params if page == 0 else None)
            data = resp.json()
            if resp.status_code != 200 or data.get("object") == "error":
                break  # no matches - return whatever was already collected
            results.extend(data.get("data", []))
            url = data.get("next_page") if data.get("has_more") else None
            page += 1
        return results[:limit]

    def get_card_by_set_number(self, set_code: str, collector_number: str) -> dict:
        """Direct single-printing lookup - the precise route when OCR has read
        both the set code and collector number off the card. Unlike a name
        search, this can never be ambiguous across reprints."""
        resp = self._request("GET", f"/cards/{set_code.lower()}/{collector_number}")
        data = resp.json()
        if resp.status_code != 200 or data.get("object") == "error":
            raise ScryfallError(
                f"get_card_by_set_number({set_code!r}, {collector_number!r}): "
                f"{data.get('details', resp.text)}"
            )
        return data

    def search_by_query(self, query: str, limit: int = 175) -> list[dict]:
        """Raw Scryfall search query (e.g. 'set:cmr sol ring') - narrower than
        a name-only fuzzy match when OCR has a set code but no collector
        number. Reuses the same pagination as search_by_name."""
        return self._search_all(query, limit)

    def get_cards_collection(self, ids: list[str]) -> list[dict]:
        """Batch-fetch cards by id, chunked at Scryfall's 75-id-per-call limit."""
        found: list[dict] = []
        for start in range(0, len(ids), COLLECTION_BATCH_SIZE):
            chunk = ids[start : start + COLLECTION_BATCH_SIZE]
            resp = self._request(
                "POST",
                "/cards/collection",
                json={"identifiers": [{"id": cid} for cid in chunk]},
            )
            data = resp.json()
            if resp.status_code != 200:
                raise ScryfallError(f"get_cards_collection: {data.get('details', resp.text)}")
            found.extend(data.get("data", []))
            # data.get("not_found") lists identifiers Scryfall couldn't match; callers
            # that care can compare the ids they sent against the ids they got back.
        return found


# --- module-level default client, so callers don't need to manage one -----

_default: ScryfallClient | None = None


def _client() -> ScryfallClient:
    global _default
    if _default is None:
        _default = ScryfallClient()
    return _default


def get_card(card_id: str) -> dict:
    return _client().get_card(card_id)


def search_by_name(text: str, limit: int = 175) -> list[dict]:
    return _client().search_by_name(text, limit)


def get_cards_collection(ids: list[str]) -> list[dict]:
    return _client().get_cards_collection(ids)


def get_card_by_set_number(set_code: str, collector_number: str) -> dict:
    return _client().get_card_by_set_number(set_code, collector_number)


def search_by_query(query: str, limit: int = 175) -> list[dict]:
    return _client().search_by_query(query, limit)
