"""Tests for scripts/scan.py's pure text/box-picking logic - no OCR model and
no network involved, so these run in the normal fast suite. `scan()` itself
(which calls easyocr and the live API) is exercised manually, not here.
"""
from __future__ import annotations

from scripts.scan import clean_name, find_set_and_number, pick_name_box


def _box(x0, y0, x1, y1, text, confidence):
    bbox = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return (bbox, text, confidence)


def test_clean_name_strips_leading_mana_cost():
    assert clean_name("{2}{U} Brainstorm") == "Brainstorm"


def test_clean_name_strips_trailing_noise():
    assert clean_name("Sol Ring {1}") == "Sol Ring"


def test_clean_name_leaves_plain_name_untouched():
    assert clean_name("Lightning Bolt") == "Lightning Bolt"


def test_pick_name_box_prefers_top_band_over_first_result():
    # The first box EasyOCR returns (reading order) is flavor text near the
    # bottom, not the title - the naive `results[0]` baseline would pick this.
    flavor = _box(10, 800, 300, 830, "Ember hound of the deep", 0.99)
    title = _box(10, 20, 300, 60, "Lightning Bolt", 0.90)
    boxes = [flavor, title]

    assert pick_name_box(boxes, image_height_hint=1000) == "Lightning Bolt"


def test_pick_name_box_breaks_ties_by_area_within_top_band():
    small_symbol = _box(10, 10, 30, 30, "R", 0.95)
    title = _box(10, 15, 250, 55, "Lightning Bolt", 0.95)
    boxes = [small_symbol, title]

    assert pick_name_box(boxes, image_height_hint=1000) == "Lightning Bolt"


def test_pick_name_box_falls_back_when_nothing_in_top_band():
    only_box = _box(10, 900, 300, 930, "Sol Ring", 0.8)
    assert pick_name_box([only_box], image_height_hint=1000) == "Sol Ring"


def test_pick_name_box_empty_input_returns_none():
    assert pick_name_box([]) is None


def test_find_set_and_number_matches_collector_number_and_nearby_set_code():
    boxes = [
        _box(10, 20, 300, 60, "Sol Ring", 0.9),
        _box(10, 950, 80, 970, "CMR", 0.6),
        _box(90, 950, 160, 970, "472/361", 0.6),
    ]
    set_code, collector_number = find_set_and_number(boxes)
    assert set_code == "cmr"
    assert collector_number == "472"


def test_find_set_and_number_returns_none_when_absent():
    boxes = [_box(10, 20, 300, 60, "Sol Ring", 0.9)]
    assert find_set_and_number(boxes) == (None, None)


def test_find_set_and_number_modern_two_line_footer():
    # Modern layout: "0472 R" on one line, "CMR • EN" on the next.
    boxes = [
        _box(10, 940, 80, 955, "0472", 0.8),
        _box(10, 960, 120, 975, "CMR • EN", 0.7),
    ]
    assert find_set_and_number(boxes) == ("cmr", "472")


def test_find_set_and_number_ignores_artist_name():
    boxes = [
        _box(10, 940, 80, 955, "123/280", 0.8),
        _box(10, 960, 200, 975, "Illus. John Avon", 0.9),
    ]
    assert find_set_and_number(boxes) == (None, "123")


def test_find_set_and_number_ignores_low_confidence_reads():
    boxes = [
        _box(10, 940, 80, 955, "123/280", 0.2),
        _box(10, 960, 120, 975, "CMR", 0.2),
    ]
    assert find_set_and_number(boxes) == (None, None)


def _fake_scan(monkeypatch, boxes, api):
    from scripts import scan as scan_mod

    class Img:
        shape = (1000, 700, 3)

    monkeypatch.setattr(scan_mod, "_load_image", lambda p: Img())
    monkeypatch.setattr(scan_mod, "_read_all", lambda img: boxes)
    monkeypatch.setattr(scan_mod, "_api_search", api)
    return scan_mod


def test_scan_exact_footer_match_skips_name_search(monkeypatch):
    boxes = [
        _box(10, 20, 300, 60, "Sol Ring", 0.9),
        _box(10, 960, 80, 975, "CMR", 0.9),
        _box(90, 960, 160, 975, "472/361", 0.9),
    ]
    calls = []

    def api(base, params):
        calls.append(params)
        return [{"id": "x"}]

    candidates, exact = _fake_scan(monkeypatch, boxes, api).scan("photo.jpg", "http://x")
    assert exact and candidates == [{"id": "x"}]
    assert calls == [{"set_code": "cmr", "collector_number": "472"}]


def test_scan_falls_back_to_title_when_footer_misses(monkeypatch):
    boxes = [_box(10, 20, 300, 60, "Sol Ring", 0.9)]
    calls = []

    def api(base, params):
        calls.append(params)
        return [{"id": "a"}, {"id": "b"}]

    candidates, exact = _fake_scan(monkeypatch, boxes, api).scan("photo.jpg", "http://x")
    assert not exact and len(candidates) == 2
    assert calls == [{"q": "Sol Ring"}]


def test_scan_unmatched_footer_falls_back_to_title_narrowed_to_set(monkeypatch):
    boxes = [
        _box(10, 20, 300, 60, "Sol Ring", 0.9),
        _box(10, 960, 80, 975, "CMR", 0.9),
        _box(90, 960, 160, 975, "472", 0.9),
    ]
    responses = iter([[], [{"id": "a"}]])
    calls = []

    def api(base, params):
        calls.append(params)
        return next(responses)

    candidates, exact = _fake_scan(monkeypatch, boxes, api).scan("photo.jpg", "http://x")
    assert not exact and candidates == [{"id": "a"}]
    assert calls[1] == {"q": "Sol Ring", "set_code": "cmr"}


# Real boxes from whole-photo OCR of the three test photos (1200px wide).
def test_find_set_and_number_real_toploader_card():
    boxes = [
        _box(340, 464, 560, 500, "Wizard s Staff", 0.68),
        _box(321, 1121, 450, 1150, "Equip Wizard", 0.99),
        _box(305, 1221, 380, 1236, "R0059", 0.71),
        _box(301, 1237, 400, 1252, "Hob .EN", 0.64),
        _box(402, 1234, 520, 1250, "Gadoltis", 0.14),
        _box(831, 1225, 870, 1240, "MEE", 0.40),
    ]
    assert find_set_and_number(boxes) == ("hob", "59")
    assert pick_name_box(boxes) == "Wizard s Staff"


def test_find_set_and_number_real_promo_card_ignores_stat_boxes():
    boxes = [
        _box(217, 147, 700, 190, "Bilbo. Luckwearer", 0.93),
        _box(338, 1322, 360, 1345, "3", 0.74),
        _box(980, 1330, 1010, 1350, "11", 0.36),
        _box(210, 1402, 280, 1420, "0002", 0.93),
        _box(312, 1402, 420, 1420, "Piomd", 0.18),
        _box(178, 1428, 240, 1445, "PRM", 0.65),
        _box(262, 1428, 300, 1445, "EN", 0.65),
    ]
    assert find_set_and_number(boxes) == ("prm", "2")


def test_find_set_and_number_old_card_without_number():
    boxes = [
        _box(265, 283, 500, 320, "Recycle", 0.98),
        _box(241, 1293, 500, 1315, "Illus. Phil Foglio", 0.87),
        _box(242, 1327, 480, 1345, "01997 Wizards of the Coast,", 0.82),
    ]
    assert find_set_and_number(boxes) == (None, None)
    assert pick_name_box(boxes) == "Recycle"


def test_find_set_and_number_ignores_copyright_mark_on_same_footer():
    # Real boxes from a Hobbit card: "MEE" (copyright) sits just above/right of
    # the set line, and HOB / EN differ in box height by a couple of pixels.
    boxes = [
        _box(156, 1380, 262, 1406, "R 0059", 0.71),
        _box(952, 1372, 1008, 1398, "MEE", 1.0),
        _box(156, 1406, 220, 1430, "HOB", 0.99),
        _box(234, 1404, 274, 1430, "EN", 1.0),
        _box(312, 1402, 424, 1428, "GABOLEPS", 0.98),
    ]
    assert find_set_and_number(boxes) == ("hob", "59")
