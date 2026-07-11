"""Tests for the re-crop QA/remap tooling and the render freshness gate."""

from __future__ import annotations

import time

from mangaeasy.images.thumbnail_compose import block_arrow_polygon
from mangaeasy.panels.cutcheck import parse_forced_cuts, window_bounds
from mangaeasy.panels.remap import is_regular_panel, map_spans
from mangaeasy.video_pipeline.check_items import is_speakable
from mangaeasy.video_pipeline.item_video_builder import stale_reason


def _span(file, top, bottom):
    return {"file": file, "top": top, "bottom": bottom}


def test_map_spans_one2one_exact():
    mapping, orphans = map_spans([_span("a.jpg", 0, 100)], [_span("n1.jpg", 0, 100)])
    assert orphans == []
    assert mapping[0]["class"] == "one2one"
    assert mapping[0]["constituents"][0]["old"] == "a.jpg"


def test_map_spans_merge_of_two_old_panels():
    old = [_span("a.jpg", 0, 100), _span("b.jpg", 100, 250)]
    mapping, orphans = map_spans(old, [_span("n1.jpg", 0, 250)])
    assert orphans == []
    assert mapping[0]["class"] == "merge"
    assert [c["old"] for c in mapping[0]["constituents"]] == ["a.jpg", "b.jpg"]


def test_map_spans_shifted_boundary():
    # Old panel 0-100 vs new panel 0-130: same content, boundary drifted.
    mapping, orphans = map_spans([_span("a.jpg", 0, 100)], [_span("n1.jpg", 0, 130)])
    assert mapping[0]["class"] == "shift"
    assert orphans == []


def test_map_spans_split_assigns_each_half_once():
    # One old panel split into two new panels: each new panel claims the old
    # one at most via >=50% overlap, so the text is never duplicated.
    old = [_span("a.jpg", 0, 100), _span("b.jpg", 100, 200)]
    new = [_span("n1.jpg", 0, 120), _span("n2.jpg", 120, 200)]
    mapping, orphans = map_spans(old, new)
    claimed = [c["old"] for m in mapping for c in m["constituents"]]
    assert sorted(claimed) == ["a.jpg", "b.jpg"]
    assert orphans == []


def test_map_spans_orphan_reported():
    mapping, orphans = map_spans(
        [_span("a.jpg", 0, 100), _span("gone.jpg", 5000, 5100)],
        [_span("n1.jpg", 0, 100)],
    )
    assert orphans == ["gone.jpg"]


def test_is_regular_panel_excludes_hook_and_cta_copies():
    assert is_regular_panel("ch01_042", "ch01_")
    assert not is_regular_panel("ch01_000_hook1", "ch01_")
    assert not is_regular_panel("ch07_999_cta", "ch07_")
    assert not is_regular_panel("ch02_001", "ch01_")


def test_parse_forced_cuts_and_window_bounds():
    assert parse_forced_cuts({"forced_cuts": ["y=123 e=45", "y=9 e=1"]}) == [123, 9]
    assert parse_forced_cuts({}) == []
    assert window_bounds(100, 200, 5000, 650) == (0, 850)
    assert window_bounds(4900, 4950, 5000, 650) == (4250, 5000)


def test_stale_reason_detects_newer_input(tmp_path):
    video = tmp_path / "item_01.mp4"
    video.write_bytes(b"v")
    old_input = tmp_path / "old.wav"
    old_input.write_bytes(b"a")
    past = time.time() - 3600
    import os
    os.utime(old_input, (past, past))
    assert stale_reason(video.stat().st_mtime, [old_input]) is None

    newer = tmp_path / "narration.json"
    newer.write_text("[]")
    future = time.time() + 3600
    os.utime(newer, (future, future))
    assert stale_reason(video.stat().st_mtime, [old_input, newer]) == "narration.json"
    # Missing inputs are ignored — freshness gate, not validation.
    assert stale_reason(video.stat().st_mtime, [tmp_path / "missing.json"]) is None


def test_is_speakable():
    assert is_speakable("The tiger queen freezes.")
    assert is_speakable("Chapter 7")
    assert not is_speakable("'?!'")
    assert not is_speakable("...")
    assert not is_speakable("")


def test_block_arrow_polygon_tip_and_shape():
    pts = block_arrow_polygon(0, 0, 100, 0, 20)
    assert len(pts) == 7
    tip = pts[3]
    assert abs(tip[0] - 100) < 1e-6 and abs(tip[1]) < 1e-6
    # Head must be wider than the shaft.
    ys = [p[1] for p in pts]
    assert max(ys) > 10 and min(ys) < -10
