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

Order of operations: the footer ("SET · 123/280") is read first and looked up
by set code + collector number, which identifies exactly one printing. Only if
that read is missing, low-confidence, or matches nothing is the title read and
used for a name search that the user picks from.

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

Set code / collector number: the tiny footer line is a hard OCR target, so
boxes under a confidence threshold are ignored and a miss simply falls
through to the title path - a wrong guess costs one extra lookup, never a
wrong card, because set code + number must resolve to exactly one printing.
"""
from __future__ import annotations

import argparse
import re
import webbrowser

import httpx

# One OCR pass over the whole photo, at this width. Measured on real photos:
# ~5-6s on CPU, and still large enough to read the tiny footer line. Going
# lower (800px) or cropping to bands loses the footer; the card rarely fills
# the frame (toploaders, hands, a table), so image-relative bands miss it.
_TARGET_WIDTH = 1200

_reader = None


# EasyOCR's model download + init is slow (several seconds) - only pay that
# cost if this script is actually run, not on every `import scripts.scan`,
# and only once per process.
def _get_reader():
    global _reader
    if _reader is None:
        import easyocr

        _reader = easyocr.Reader(["en"], gpu=False)
    return _reader


def _load_image(image_path: str):
    """Read a card photo and resize it to _TARGET_WIDTH wide. Small photos are
    upscaled, not left alone: the footer text is only legible to EasyOCR at
    roughly this scale (a 768px-wide photo read nothing until upscaled)."""
    import cv2

    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"could not read image: {image_path}")
    h, w = img.shape[:2]
    if w != _TARGET_WIDTH:
        interp = cv2.INTER_AREA if w > _TARGET_WIDTH else cv2.INTER_CUBIC
        img = cv2.resize(img, (_TARGET_WIDTH, round(h * _TARGET_WIDTH / w)), interpolation=interp)
    return img


def _read_all(img) -> list[tuple[list, str, float]]:
    return [(bbox, text, float(conf)) for bbox, text, conf in _get_reader().readtext(img)]


_LEADING_MANA = re.compile(r"^\s*(\{[^}]*\}|\d+)+\s*")
_TRAILING_NOISE = re.compile(r"[\d\{\}].*$")
# Two footer shapes. Older: "123/280". Modern: zero-padded "0123", often with
# the rarity letter glued on by OCR ("R0059"). Bare 1-2 digit boxes are never
# treated as numbers - on the full image those are power/toughness, mana
# symbols, etc. A suffix letter covers variants like "123a".
_NUMBER_WITH_TOTAL = re.compile(r"^\s*(\d{1,4}[a-z★]?)\s*/\s*\d{1,4}\s*$", re.IGNORECASE)
_NUMBER_PADDED = re.compile(r"^\s*[A-Za-z]?\s?(\d{3,4}[a-z★]?)\s*$")
_ALNUM_TOKEN = re.compile(r"[A-Za-z0-9]+")
_NOT_SET_CODES = {"ILLUS", "EN"}
# Below this OCR confidence a footer read counts as "fuzzy" - better to fall
# back to the title than to trust a guess at tiny print.
_MIN_FOOTER_CONF = 0.4


def clean_name(raw: str) -> str:
    """Strip mana-cost clusters and trailing noise OCR tends to glue onto a
    card name, e.g. '{2}{U} Brainstorm' -> 'Brainstorm', 'Sol Ring {1}' ->
    'Sol Ring'."""
    text = _LEADING_MANA.sub("", raw)
    text = _TRAILING_NOISE.sub("", text)
    return text.strip()


def _top(bbox) -> float:
    return min(pt[1] for pt in bbox)


def pick_name_box(
    boxes: list[tuple[list, str, float]], image_height_hint: float | None = None
) -> str | None:
    """Pick the box most likely to be the card's title.

    The title is the topmost real text on the card. On a whole-photo OCR pass
    the card sits somewhere inside the frame, so "top 20% of the image" is
    wrong (a card in a toploader starts well down the photo); instead take the
    topmost box that looks like words - 3+ letters, not a low-confidence
    guess - which skips mana-cost glyphs and set symbols read as stray
    characters. Falls back to the single highest-confidence box if nothing
    qualifies. `image_height_hint` is kept for callers; it is unused now.
    """
    if not boxes:
        return None

    def wordlike(b) -> bool:
        return b[2] >= 0.3 and sum(ch.isalpha() for ch in b[1]) >= 3

    pool = [b for b in boxes if wordlike(b)]
    if pool:
        return min(pool, key=lambda b: _top(b[0]))[1]
    return max(boxes, key=lambda b: b[2])[1]


def _row_tokens(boxes, row_gap: float) -> list[str]:
    """Alphanumeric tokens from `boxes` in reading order. Boxes whose vertical
    centres are within `row_gap` of the previous box share a line, then each
    line is read left to right. (Bucketing by each box's own height instead
    splits one printed line across rows and scrambles the order.)"""
    items = []
    for bbox, text, _ in boxes:
        ys = [pt[1] for pt in bbox]
        items.append(((min(ys) + max(ys)) / 2, min(pt[0] for pt in bbox), text))
    items.sort()

    rows: list[list[tuple[float, str]]] = []
    last_cy = None
    for cy, x, text in items:
        if last_cy is None or cy - last_cy > row_gap:
            rows.append([])
        rows[-1].append((x, text))
        last_cy = cy

    return [tok for row in rows for _, text in sorted(row) for tok in _ALNUM_TOKEN.findall(text)]


def find_set_and_number(boxes: list[tuple[list, str, float]]) -> tuple[str | None, str | None]:
    """Best-effort read of the footer's collector number and set code from
    whole-photo OCR boxes. Low-confidence boxes are ignored (a fuzzy read is
    treated as no read). Returns (None, None) where there is no signal -
    callers should treat that as "fall back to the title", not an error.

    The footer is the lowest number-shaped text on the card, so the number is
    the lowest matching box. The set code is then looked for only in boxes near
    it: the token just before the language code ("PRM" in "PRM • EN"), or -
    for layouts without one - an all-caps 3-5 letter token on the same line.
    """
    confident = [b for b in boxes if b[2] >= _MIN_FOOTER_CONF]

    number_box = collector_number = None
    for box in confident:
        m = _NUMBER_WITH_TOTAL.match(box[1]) or _NUMBER_PADDED.match(box[1])
        if m and (number_box is None or _top(box[0]) > _top(number_box[0])):
            number_box = box
            # Scryfall stores "123", not "0123".
            collector_number = m.group(1).lstrip("0") or "0"
    if number_box is None:
        return None, None

    ys = [pt[1] for pt in number_box[0]]
    height = max(max(ys) - min(ys), 1)
    cy = (min(ys) + max(ys)) / 2
    near = [
        b
        for b in confident
        if b is not number_box and -height <= (_top(b[0]) + height / 2) - cy <= 4 * height
    ]

    tokens = _row_tokens(near, row_gap=height / 2)
    set_code = None
    for i, tok in enumerate(tokens):
        if tok.upper() == "EN" and i > 0 and 3 <= len(tokens[i - 1]) <= 5:
            set_code = tokens[i - 1].lower()
            break
    if set_code is None:
        for b in near:
            for tok in _ALNUM_TOKEN.findall(b[1]):
                if tok.isupper() and 3 <= len(tok) <= 5 and tok not in _NOT_SET_CODES:
                    set_code = tok.lower()
                    break
            if set_code:
                break

    return set_code, collector_number


def _api_search(api_base: str, params: dict) -> list[dict]:
    resp = httpx.get(f"{api_base}/api/search", params=params, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("error") == "offline":
        raise RuntimeError(
            "the app is offline and this card isn't cached yet - connect to wifi and retry"
        )
    return data


def scan(image_path: str, api_base: str) -> tuple[list[dict], bool]:
    """Identify a card photo. Returns (candidates, exact).

    exact=True means the footer's set code + collector number resolved to
    exactly one printing - no need to ask the user. Otherwise (footer missing,
    fuzzy, or no such printing) candidates come from a name search on the
    title, for the user to pick from. Both come from one OCR pass over the
    photo; the footer lookup just takes priority, and the title search only
    runs if it doesn't settle the card.
    """
    boxes = _read_all(_load_image(image_path))

    set_code, collector_number = find_set_and_number(boxes)
    if set_code and collector_number:
        hits = _api_search(api_base, {"set_code": set_code, "collector_number": collector_number})
        if len(hits) == 1:
            return hits, True

    raw_name = pick_name_box(boxes)
    if not raw_name:
        return [], False
    params = {"q": clean_name(raw_name)}
    if set_code:
        # Narrows the name search if the set was right; the number already
        # failed above, so it isn't sent again.
        params["set_code"] = set_code
    return _api_search(api_base, params), False


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

    candidates, exact = scan(args.image, args.api_base)
    if not candidates:
        print("No candidates found - OCR couldn't read a usable set/number or name.")
        return

    if exact:
        print("Exact match (set code + collector number):\n")
    else:
        print(f"{len(candidates)} candidate(s) from the title - pick one:\n")
    for c in candidates:
        print(
            f"  {c['name']:<30} {c['set_code'] or '?':<5} "
            f"#{c['collector_number'] or '?':<6} ${c['price_usd'] or 'n/a'}"
        )

    if args.open:
        webbrowser.open(f"{args.api_base}/cards/{candidates[0]['id']}")


if __name__ == "__main__":
    main()
