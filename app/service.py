"""The seam between app/scryfall.py and app/store/.

Routes and (later) templates call functions here; whether a read is served
from SQLite or falls through to Scryfall stays invisible to them.
"""
from __future__ import annotations

from datetime import datetime, timezone

from rapidfuzz import fuzz

from . import config, scryfall, store


def search(
    text: str,
    limit: int = 10,
    set_code: str | None = None,
    collector_number: str | None = None,
) -> list[dict]:
    """Return the most likely printings for `text`, best match first.

    Each candidate is scored by name similarity to `text` - this is what
    matters when the fuzzy match itself was uncertain (garbled OCR, a typo).
    When Scryfall's fuzzy match succeeds, every printing shares the same name
    and the score ties; recency breaks the tie, since there's no way to tell
    which specific printing was scanned from a name alone (that's on the user
    to pick from the shortlist).

    `set_code` / `collector_number` are the real fix for that tie, when OCR
    can read them off the card: with both, this resolves the exact printing
    directly (no ambiguity possible) and returns just that one candidate. If
    that direct lookup 404s (a misread number), it falls back to a name
    search narrowed to `set_code` rather than returning nothing - the set
    code may still be good even if the number wasn't. With only `set_code`
    (no number), the same narrowed query is used, falling back further to a
    plain name search if even that finds nothing.
    """
    if set_code and collector_number:
        try:
            return [_to_candidate(scryfall.get_card_by_set_number(set_code, collector_number))]
        except scryfall.ScryfallOffline:
            raise
        except scryfall.ScryfallError:
            pass  # misread number - fall through to the name search below

    if set_code:
        candidates = scryfall.search_by_query(f"set:{set_code} {text}")
        if not candidates:
            candidates = scryfall.search_by_name(text)
    else:
        candidates = scryfall.search_by_name(text)

    scored = [
        (fuzz.WRatio(text, card.get("name", "")), card.get("released_at") or "", card)
        for card in candidates
    ]
    scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [_to_candidate(card) for _, _, card in scored[:limit]]


def _to_candidate(card: dict) -> dict:
    """Trim a full Scryfall card object down to what a picker UI needs."""
    image = None
    if isinstance(card.get("image_uris"), dict):
        image = card["image_uris"].get("normal")
    return {
        "id": card.get("id"),
        "name": card.get("name"),
        "set_code": card.get("set"),
        "set_name": card.get("set_name"),
        "collector_number": card.get("collector_number"),
        "rarity": card.get("rarity"),
        "released_at": card.get("released_at"),
        "image_uri": image,
        "price_usd": (card.get("prices") or {}).get("usd"),
    }


def get_card(card_id: str) -> dict:
    """The cache-aside read: serve from SQLite, falling through to Scryfall on
    a cache miss or when the stored price is older than PRICE_TTL_DAYS.

    Offline handling: a cache MISS with no network raises `ScryfallOffline` -
    there is nothing to fall back to, so the caller (eventually the UI) should
    prompt to connect. A STALE price with no network is not fatal - we already
    have a card to show, so we serve the stale cached price rather than fail
    outright. Any other Scryfall error (a real API error, not connectivity)
    still propagates in both cases.

    Returns the cached card's columns merged with its current prices.
    """
    card = store.get_card(card_id)
    if card is None:
        data = scryfall.get_card(card_id)  # nothing cached - offline must raise
        store.upsert_card(data)
        store.upsert_prices(data)
        card = store.get_card(card_id)
    else:
        age = store.price_age_days(card_id)
        if age is None or age > config.PRICE_TTL_DAYS:
            try:
                store.upsert_prices(scryfall.get_card(card_id))
            except scryfall.ScryfallOffline:
                pass  # offline - serve what's already cached instead of failing

    prices = store.get_prices(card_id) or {}
    return {**card, **{k: v for k, v in prices.items() if k != "card_id"}}


def add_owned(
    card_id: str,
    quantity: int = 1,
    finish: str = "nonfoil",
    condition: str = "",
) -> dict:
    """Record ownership of a card, making sure it's cached (and priced) first."""
    get_card(card_id)
    return store.add_to_collection(card_id, quantity=quantity, finish=finish, condition=condition)


def refresh_prices() -> int:
    """Batch-refresh every stale price for cards actually in the collection.

    Returns how many cards were refreshed. This is the weekly job - it never
    touches cards you don't own, and never fetches static card data, only
    prices. Records `last_refresh_at` in the `meta` table (the UI's "last
    refreshed <time>" line) - only on a successful run, so an offline attempt
    that raises doesn't claim a refresh that didn't happen.
    """
    ids = store.stale_card_ids(config.PRICE_TTL_DAYS, only_collection=True)
    refreshed = 0
    if ids:
        for data in scryfall.get_cards_collection(ids):  # already chunks at 75 internally
            store.upsert_prices(data)
            refreshed += 1
    store.set_meta("last_refresh_at", datetime.now(timezone.utc).isoformat())
    return refreshed
