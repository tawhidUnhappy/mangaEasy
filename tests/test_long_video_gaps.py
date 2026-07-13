"""included_chapters decides which item videos the long-video join stitches.

Keys are item NAMES so split/extra chapters (2.1, 9.5) join in value order
instead of silently vanishing. Contiguity (gap detection) stays integer-only:
strict mode treats an integer hole as a failed render; --allow-gaps skips it.
"""

from pathlib import Path

from mangaeasy.video_pipeline.long_video_builder import ITEM_VIDEO_RE, included_chapters


def _chapters(*names: str) -> dict[str, Path]:
    return {name: Path(f"item_{name}.mp4") for name in names}


def test_contiguous_range_has_no_gaps_either_mode():
    chapters = _chapters("01", "02", "03")
    for allow in (False, True):
        names, gaps = included_chapters(chapters, 1, 3, allow_gaps=allow)
        assert names == ["01", "02", "03"]
        assert gaps == []


def test_integer_gap_is_reported():
    names, gaps = included_chapters(_chapters("01", "03", "04"), 1, 4, allow_gaps=True)
    assert names == ["01", "03", "04"]
    assert gaps == [2]


def test_multiple_holes():
    names, gaps = included_chapters(_chapters("01", "03", "05"), 1, 5, allow_gaps=True)
    assert names == ["01", "03", "05"]
    assert gaps == [2, 4]


def test_chapters_outside_range_are_excluded():
    names, _ = included_chapters(_chapters("01", "03", "12", "13"), 1, 12, allow_gaps=True)
    assert "13" not in names


def test_decimal_chapters_join_in_value_order():
    # 9.5 must ride between 09 and 10 — it used to vanish from the join.
    names, gaps = included_chapters(_chapters("09", "9.5", "10"), 9, 10, allow_gaps=False)
    assert names == ["09", "9.5", "10"]
    assert gaps == []


def test_split_chapters_do_not_mask_a_missing_integer():
    # 2.1/2.2 exist but integer 02 does not: that IS a gap (2.1 is not "2").
    names, gaps = included_chapters(_chapters("01", "2.1", "2.2", "03"), 1, 3, allow_gaps=True)
    assert names == ["01", "2.1", "2.2", "03"]
    assert gaps == [2]


def test_item_video_regex_accepts_decimals():
    assert ITEM_VIDEO_RE.match("item_9.5.mp4").group(1) == "9.5"
    assert ITEM_VIDEO_RE.match("item_01.mp4").group(1) == "01"
    assert ITEM_VIDEO_RE.match("item_01.old.mp4") is None
