"""Tests for webtoon-override index resolution and narration-edit upsert logic."""

from __future__ import annotations

import pytest

from mangaeasy.panels.overrides_tool import (
    coalesce_merges,
    resolve_merge_at_cut,
    resolve_merge_panels,
)
from mangaeasy.video_pipeline.narration_edit import sorted_insert_position, upsert


def _panels(*spans):
    return [{"index": i, "top": t, "bottom": b} for i, (t, b) in enumerate(spans, 1)]


BASE = _panels((0, 100), (100, 250), (250, 400), (500, 700))


def test_resolve_merge_at_cut_finds_adjacent_pair():
    assert resolve_merge_at_cut(BASE, 100) == [0, 1]
    assert resolve_merge_at_cut(BASE, 251) == [1, 2]  # within tolerance


def test_resolve_merge_at_cut_rejects_gap_boundary():
    # 400/500 is a dropped gap, not an auto-split cut — merging there is wrong.
    with pytest.raises(ValueError):
        resolve_merge_at_cut(BASE, 400)


def test_resolve_merge_at_cut_rejects_unknown_y():
    with pytest.raises(ValueError):
        resolve_merge_at_cut(BASE, 175)


def test_resolve_merge_panels_translates_final_numbers_to_base():
    # After an earlier merge of base panels 1+2, current final has 3 panels;
    # fusing final #1..#2 must expand to base span [0, 2].
    final = _panels((0, 250), (250, 400), (500, 700))
    assert resolve_merge_panels(BASE, final, 1, 2) == [0, 2]


def test_resolve_merge_panels_rejects_single_base_panel():
    final = _panels((0, 100), (100, 250))
    with pytest.raises(ValueError):
        resolve_merge_panels(_panels((0, 250)), final, 1, 2)


def test_coalesce_merges_chains_and_overlaps():
    assert coalesce_merges([[5, 6], [1, 2], [2, 3]]) == [[1, 3], [5, 6]]
    assert coalesce_merges([]) == []
    assert coalesce_merges([[4, 5]]) == [[4, 5]]


def test_upsert_replaces_and_reports_previous():
    entries = [{"image": "ch01_001.jpg", "narration": "old"}]
    entries, previous = upsert(entries, "ch01_001.jpg", "new")
    assert previous == "old"
    assert entries[0]["narration"] == "new"


def test_upsert_inserts_in_name_sorted_reading_order():
    entries = [
        {"image": "ch01_000_hook1.jpg", "narration": "h"},
        {"image": "ch01_001.jpg", "narration": "a"},
        {"image": "ch01_003.jpg", "narration": "c"},
    ]
    entries, previous = upsert(entries, "ch01_002.jpg", "b")
    assert previous is None
    assert [e["image"] for e in entries] == [
        "ch01_000_hook1.jpg", "ch01_001.jpg", "ch01_002.jpg", "ch01_003.jpg"]
    # CTA copies (page 999) always land last.
    assert sorted_insert_position(entries, "ch01_999_cta.jpg") == len(entries)
