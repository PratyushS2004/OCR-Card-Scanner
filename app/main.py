"""FastAPI routes + Jinja templates - the browse/search UI.

Thin: every route just calls into `service` or `store` and renders a
template. Whether a read came from SQLite or fell through to Scryfall stays
invisible here too, same as it is in service.py - except for the two routes
where an offline cache MISS has nothing to show (`/search`, `/cards/{id}`),
which catch `ScryfallOffline` specifically and render a "you're offline"
notice instead of a generic error page.
"""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from . import config, db, service, store
from .scryfall import ScryfallOffline

MANA_SYMBOL_RE = re.compile(r"\{([^{}]+)\}")
REMINDER_TEXT_RE = re.compile(r"\s*\([^()]*\)")


def strip_reminder_text(text: str | None) -> str | None:
    """Drop Magic's parenthetical reminder text - it restates what the ability
    already says, so it's redundant once you know the rules."""
    return REMINDER_TEXT_RE.sub("", text) if text else text


RARITY_LETTERS = {
    "common": "C",
    "uncommon": "U",
    "rare": "R",
    "mythic": "M",
    "special": "S",
    "bonus": "B",
}


def rarity_letter(rarity: str | None) -> str:
    if not rarity:
        return ""
    return RARITY_LETTERS.get(rarity.lower(), rarity[:1].upper())


def pad_collector_number(number: str | None) -> str:
    """Match the 4-digit zero-padded collector number printed on the card
    itself (e.g. "205" -> "0205"); non-numeric collector numbers (promos,
    special suffixes) are left as-is."""
    if not number:
        return ""
    return number.zfill(4) if number.isdigit() else number


def mana_symbols(text: str | None) -> Markup:
    """Render {T}/{1}/{W/U}-style mana cost tokens as Scryfall's icon SVGs.

    Scryfall's CDN names each icon by the symbol with braces and the "/"
    (in hybrid/Phyrexian symbols like {W/U}) stripped - e.g. {T} -> T.svg,
    {W/U} -> WU.svg. Verified against Scryfall's /symbology endpoint.
    """
    if not text:
        return Markup("")
    pieces: list[str | Markup] = []
    last = 0
    for match in MANA_SYMBOL_RE.finditer(text):
        pieces.append(text[last : match.start()])  # plain str - Markup.join escapes it below
        token = match.group(1)
        filename = escape(token.replace("/", ""))
        label = escape(match.group(0))
        pieces.append(
            Markup(
                f'<img class="mana-symbol" src="https://svgs.scryfall.io/card-symbols/{filename}.svg" '
                f'alt="{label}" title="{label}">'
            )
        )
        last = match.end()
    pieces.append(text[last:])
    return Markup("").join(pieces)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(config.BASE_DIR / "templates"))
templates.env.filters["mana_symbols"] = mana_symbols
templates.env.filters["strip_reminder_text"] = strip_reminder_text
templates.env.filters["rarity_letter"] = rarity_letter
templates.env.filters["pad_collector_number"] = pad_collector_number
# Cache-bust /static/style.css by its mtime, so an edit is picked up on the
# next request instead of serving whatever the browser cached previously.
templates.env.globals["static_version"] = int(
    (config.BASE_DIR / "static" / "style.css").stat().st_mtime
)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html", {"query": "", "results": [], "offline": False}
    )


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = ""):
    results: list[dict] = []
    offline = False
    if q.strip():
        try:
            results = service.search(q)
        except ScryfallOffline:
            offline = True
    context = {"query": q, "results": results, "offline": offline}
    template = "_results.html" if request.headers.get("HX-Request") else "index.html"
    return templates.TemplateResponse(request, template, context)


@app.get("/cards/{card_id}", response_class=HTMLResponse)
def card_detail(request: Request, card_id: str):
    try:
        card = service.get_card(card_id)
    except ScryfallOffline:
        return templates.TemplateResponse(request, "card.html", {"card": None, "offline": True})
    return templates.TemplateResponse(request, "card.html", {"card": card, "offline": False})


def _format_last_refresh(iso: str | None) -> str | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso).strftime("%b %d, %Y %H:%M UTC")


def _collection_context(refreshed: int | None = None) -> dict:
    """Shared context for both the full collection page and its HTMX fragment,
    so a mutation (quantity change, delete, refresh) always re-renders the
    whole panel - stats and total stay correct, never one render behind."""
    data = store.list_collection()
    rows = data["rows"]
    stats = {
        "card_count": len(rows),
        "total_quantity": sum(row["quantity"] for row in rows),
        "unknown_price_count": sum(1 for row in rows if row["unit_price"] is None),
    }
    return {
        "rows": rows,
        "total": data["total"],
        "stats": stats,
        "refreshed": refreshed,
        "last_refresh_at": _format_last_refresh(store.get_meta("last_refresh_at")),
    }


def _render_collection(request: Request, refreshed: int | None = None) -> HTMLResponse:
    """Fragment-or-full-page response, same pattern as `/search`."""
    context = _collection_context(refreshed)
    template = "_collection.html" if request.headers.get("HX-Request") else "collection.html"
    return templates.TemplateResponse(request, template, context)


@app.get("/collection", response_class=HTMLResponse)
def collection_page(request: Request, refreshed: int | None = None):
    return _render_collection(request, refreshed=refreshed)


@app.post("/collection")
def add_owned_route(
    card_id: str = Form(...),
    finish: str = Form("nonfoil"),
    quantity: int = Form(1),
    condition: str = Form(""),
):
    service.add_owned(card_id, quantity=quantity, finish=finish, condition=condition)
    return RedirectResponse(url="/collection", status_code=303)


@app.post("/collection/{collection_id}", response_class=HTMLResponse)
def update_quantity_route(request: Request, collection_id: int, quantity: int = Form(...)):
    store.set_quantity(collection_id, quantity)
    if request.headers.get("HX-Request"):
        return _render_collection(request)
    return RedirectResponse(url="/collection", status_code=303)


@app.post("/collection/{collection_id}/delete", response_class=HTMLResponse)
def delete_collection_row_route(request: Request, collection_id: int):
    store.remove_from_collection(collection_id)
    if request.headers.get("HX-Request"):
        return _render_collection(request)
    return RedirectResponse(url="/collection", status_code=303)


@app.post("/refresh-prices", response_class=HTMLResponse)
def refresh_prices_route(request: Request):
    refreshed = service.refresh_prices()
    if request.headers.get("HX-Request"):
        return _render_collection(request, refreshed=refreshed)
    return RedirectResponse(url=f"/collection?refreshed={refreshed}", status_code=303)


@app.get("/api/search")
def api_search(q: str = "", set_code: str = "", collector_number: str = ""):
    if not q.strip():
        return []
    try:
        return service.search(
            q,
            set_code=set_code.strip() or None,
            collector_number=collector_number.strip() or None,
        )
    except ScryfallOffline:
        return JSONResponse({"error": "offline"}, status_code=503)
