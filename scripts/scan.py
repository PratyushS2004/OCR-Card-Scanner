"""OCR a photo of a Magic card and look it up against this app's own API,
instead of hitting Scryfall directly - keeps OCR fully decoupled from the
cache-aside layer and lets scans work even when this machine is offline (the
API itself falls back to cached data; see app/service.py's offline handling).

Run the web app first:

    python -m uvicorn app.main:app --reload

Then, from the project root:

    python -m scripts.scan path/to/photo.jpg
    python -m scripts.scan path/to/photo.jpg --open      # open the top match in a browser
    python -m scripts.scan path/to/photo.jpg --api-base http://raspberrypi.local:8000

Name-extraction (the real accuracy lever, per ROADMAP.md Phase 6):
the original test_API.py spike just took `results[0]` - EasyOCR's first
detected box, in whatever order it happened to find text - and stripped
trailing digits/braces with a regex. Two things replace that:

- **Box selection**: a Magic card's title is the largest text near the top of
  the card, not necessarily the first box EasyOCR returns. This picks the
  highest-confidence box among those in the top ~20% of the image, breaking
  ties by bounding-box area (title text is set larger than everything else
  up there).
- **Cleanup**: strip a leading mana-cost cluster (e.g. "{2}{U}") and a
  trailing mana-cost cluster some OCR misreads glue onto the name, not just
  trailing digits.

Set code / collector number (best-effort): the tiny "SET · 123/280" line
near the bottom-left of the card is a much smaller target for OCR, so this
only forwards it when a box confidently matches the "123/280" shape - a
misread here is caught by the local API's own fallback (app/service.py
degrades a bad set+number lookup to a name-only search), so a wrong guess
here costs nothing.
"""
from __future__ import annotations

import argparse
import re
import webbrowser

import httpx

# EasyOCR's model download + init is slow (several seconds) - only pay that
# cost if this script is actually run, not on every `import scripts.scan`.
def _ocr(image_path: str) -> list[tuple[list, str, float]]:
    import easyocr

    reader = easyocr.Reader(["en"])
    return reader.readtext(image_path)


_LEADING_MANA = re.compile(r"^\s*(\{[^}]*\}|\d+)+\s*")
_TRAILING_NOISE = re.compile(r"[\d\{\}].*$")
_COLLECTOR_NUMBER = re.compile(r"^\s*(\d{1,4})\s*/\s*(\d{1,4})\s*$")
_SET_CODE = re.compile(r"^[A-Z0-9]{2,5}$")


def clean_name(raw: str) -> str:
    """Strip mana-cost clusters and trailing noise OCR tends to glue onto a
    card name, e.g. '{2}{U} Brainstorm' -> 'Brainstorm', 'Sol Ring {1}' ->
    'Sol Ring'."""
    text = _LEADING_MANA.sub("", raw)
    text = _TRAILING_NOISE.sub("", text)
    return text.strip()


def pick_name_box(
    boxes: list[tuple[list, str, float]], image_height_hint: float | None = None
) -> str | None:
    """Pick the box most likely to be the card's title.

    Falls back through progressively looser criteria so a scan still returns
    *something* rather than nothing:
    1. Highest-confidence box in the top 20% of the image (ties broken by
       bounding-box area - title text is the biggest thing up there).
    2. If nothing landed in that band (a tight crop, an unusual angle), the
       single highest-confidence box anywhere.
    """
    if not boxes:
        return None

    ys = [pt[1] for bbox, _, _ in boxes for pt in bbox]
    max_y = image_height_hint or max(ys)
    top_band = max_y * 0.2

    def area(bbox) -> float:
        xs = [pt[0] for pt in bbox]
        y_s = [pt[1] for pt in bbox]
        return (max(xs) - min(xs)) * (max(y_s) - min(y_s))

    candidates = [b for b in boxes if min(pt[1] for pt in b[0]) <= top_band]
    pool = candidates or boxes
    best = max(pool, key=lambda b: (round(b[2], 2), area(b[0])))
    return best[1]


def find_set_and_number(boxes: list[tuple[list, str, float]]) -> tuple[str | None, str | None]:
    """Best-effort scan for a 'NNN/NNN' collector number and a nearby set
    code among all detected boxes. Returns (None, None) if nothing matches -
    callers should treat that as "no signal", not an error."""
    collector_number = None
    for _, text, _ in boxes:
        m = _COLLECTOR_NUMBER.match(text)
        if m:
            collector_number = m.group(1)
            break

    set_code = None
    for _, text, _ in boxes:
        token = text.strip().upper()
        if _SET_CODE.match(token) and not _COLLECTOR_NUMBER.match(text):
            set_code = token.lower()
            break

    return set_code, collector_number


def scan(image_path: str, api_base: str) -> list[dict]:
    boxes = _ocr(image_path)
    raw_name = pick_name_box(boxes)
    if not raw_name:
        return []
    name = clean_name(raw_name)
    set_code, collector_number = find_set_and_number(boxes)

    params = {"q": name}
    if set_code:
        params["set_code"] = set_code
    if collector_number:
        params["collector_number"] = collector_number

    resp = httpx.get(f"{api_base}/api/search", params=params, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("error") == "offline":
        raise RuntimeError(
            "the app is offline and this card isn't cached yet - connect to wifi and retry"
        )
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("image", help="path to a photo of the card")
    parser.add_argument(
        "--api-base", default="http://localhost:8000", help="base URL of the running web app"
    )
    parser.add_argument(
        "--open", action="store_true", help="open the top match's card page in a browser"
    )
    args = parser.parse_args()

    candidates = scan(args.image, args.api_base)
    if not candidates:
        print("No candidates found - OCR couldn't read a usable name from the image.")
        return

    print(f"{len(candidates)} candidate(s):\n")
    for c in candidates:
        print(
            f"  {c['name']:<30} {c['set_code'] or '?':<5} "
            f"#{c['collector_number'] or '?':<6} ${c['price_usd'] or 'n/a'}"
        )

    if args.open:
        webbrowser.open(f"{args.api_base}/cards/{candidates[0]['id']}")


if __name__ == "__main__":
    main()
