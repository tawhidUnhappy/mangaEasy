"""Unit tests for the deterministic parts of `page-split` (no MAGI/GPU needed)."""

from mediaconductor.panels.ai import _manga_reading_order
from mediaconductor.panels.page import FULL_PAGE_AREA_FRAC, TALL_PANEL_ASPECT_RATIO, boxes_for_page


def test_boxes_for_page_uses_detection_panels():
    detection = {"size": [100, 200], "panels": [[0, 0, 50, 50], [50, 0, 100, 50]]}
    boxes, full_page = boxes_for_page(detection, None, 100, 200)
    assert full_page is False
    assert len(boxes) == 2
    assert all({"x1", "y1", "x2", "y2"} <= b.keys() for b in boxes)


def test_boxes_for_page_falls_back_to_whole_page():
    boxes, full_page = boxes_for_page({"panels": []}, None, 120, 340)
    assert full_page is True
    assert boxes == [{"x1": 0, "y1": 0, "x2": 120, "y2": 340}]


def test_boxes_for_page_none_detection_is_whole_page():
    boxes, full_page = boxes_for_page(None, None, 80, 90)
    assert full_page is True
    assert boxes == [{"x1": 0, "y1": 0, "x2": 80, "y2": 90}]


def test_override_replaces_detection_and_clamps():
    detection = {"panels": [[0, 0, 10, 10]]}
    # Override boxes deliberately overshoot the page; they must be clamped.
    boxes, full_page = boxes_for_page(detection, [[-5, -5, 999, 999]], 100, 100)
    assert full_page is False
    assert boxes == [{"x1": 0, "y1": 0, "x2": 100, "y2": 100}]


def test_full_page_area_fraction_is_a_sane_threshold():
    assert 0.5 < FULL_PAGE_AREA_FRAC < 1.0


def test_tall_panel_aspect_ratio_permits_square_but_flags_far_taller():
    # A 1:1 crop must not trip the threshold; only meaningfully taller ones should.
    assert TALL_PANEL_ASPECT_RATIO > 1.0
    square_ok = 1.0 < TALL_PANEL_ASPECT_RATIO
    very_tall_flagged = 4.0 >= TALL_PANEL_ASPECT_RATIO
    assert square_ok and very_tall_flagged


def test_reading_order_direction_flips_row_order():
    # Two boxes on the same horizontal band, left box then right box.
    left = {"x1": 0, "y1": 0, "x2": 40, "y2": 100}
    right = {"x1": 60, "y1": 0, "x2": 100, "y2": 100}
    rtl = _manga_reading_order([left, right], rtl=True)
    ltr = _manga_reading_order([left, right], rtl=False)
    # Right-to-left reads the right box first; left-to-right reads the left first.
    assert rtl[0] is right and rtl[1] is left
    assert ltr[0] is left and ltr[1] is right
