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
