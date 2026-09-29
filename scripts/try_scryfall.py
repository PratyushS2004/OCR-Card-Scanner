"""Manual sanity check against the REAL Scryfall API - not run by pytest.

Run from the project root:

    python -m scripts.try_scryfall "sol ring"

Makes a couple of real, rate-limited requests and prints what comes back, so
you can eyeball that app/scryfall.py + app/service.py actually work end to end.
"""
from __future__ import annotations

import sys

from app import scryfall, service


def main() -> None:
    query = " ".join(sys.argv[1:]) or "sol ring"

    print(f"All printings Scryfall knows for {query!r}...")
    raw = scryfall.search_by_name(query)
    print(f"  {len(raw)} total (unranked, newest-first from the API)\n")

    print(f"Most likely matches for {query!r} (ranked, top 10)...")
    candidates = service.search(query)
    if not candidates:
        print("  No candidates found.")
        return
    for c in candidates:
        print(f"  {c['name']:<25} {c['set_code'] or '?':<5} "
              f"#{c['collector_number'] or '?':<6} ${c['price_usd'] or 'n/a'}")

    first_id = candidates[0]["id"]
    print(f"\nFetching the top match by id ({first_id})...")
    card = scryfall.get_card(first_id)
    print(f"  {card['name']} - {card.get('type_line')}")


if __name__ == "__main__":
    main()
