"""Data access layer, split one module per table.

Everything is re-exported here, so callers use the flat API:

    from app import store
    store.get_card(...)         # -> store.cards
    store.upsert_prices(...)    # -> store.prices
    store.list_collection()     # -> store.collection

The submodules (`store.cards`, `store.prices`, `store.collection`) can also be
imported directly. All functions are synchronous and network-free.
"""
from .cards import get_card, upsert_card
from .collection import (
    add_to_collection,
    list_collection,
    remove_from_collection,
    set_quantity,
)
from .meta import get_meta, set_meta
from .prices import get_prices, price_age_days, stale_card_ids, upsert_prices
from ._common import FINISHES

__all__ = [
    # cards
    "get_card",
    "upsert_card",
    # prices
    "get_prices",
    "price_age_days",
    "stale_card_ids",
    "upsert_prices",
    # collection
    "FINISHES",
    "add_to_collection",
    "list_collection",
    "remove_from_collection",
    "set_quantity",
    # meta
    "get_meta",
    "set_meta",
]
