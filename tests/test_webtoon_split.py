"""Pure-logic tests for `webtoon-split` (mangaeasy.panels.webtoon).

Covers the three additions over plain gutter detection: auto-splitting merged
mega-panels at quiet rows, rescuing content-bearing gaps, and manual range
overrides. All operate on plain lists + numpy arrays — no image files needed.
"""

import numpy as np

from mangaeasy.panels.webtoon import (
    apply_range_overrides,
    auto_split_ranges,
    band_energy,
    find_content_gaps,
    rescue_gaps,
)

WIDTH = 800


def flat_energy(height, value=5.0):
    return np.full(height, value, dtype=np.float32)


# ---------------------------------------------------------------------------
# auto_split_ranges
# ---------------------------------------------------------------------------

def test_short_panels_pass_through_unsplit():
    energy = flat_energy(3000)
    ranges, cuts, _forced = auto_split_ranges([(0, 1500)], energy, WIDTH)
    assert ranges == [(0, 1500)]
    assert cuts == []


def test_mega_panel_gets_split():
    height = 6000  # 7.5x width, well over the 2.2 ratio
    energy = flat_energy(height)
    ranges, cuts, _forced = auto_split_ranges([(0, height)], energy, WIDTH)
    assert len(ranges) > 1
    assert len(cuts) == len(ranges) - 1
    # segments tile the original range exactly, in order
    assert ranges[0][0] == 0 and ranges[-1][1] == height
    for (_, b), (t, _) in zip(ranges, ranges[1:], strict=False):
        assert b == t


def test_cuts_snap_to_quiet_rows():
    height = 4000
    energy = flat_energy(height, 50.0)
    energy[2100] = 0.0  # one obviously quiet row near the midpoint
    # target_height=2000 -> exactly one cut, searched within +/-380 of y=2000
    ranges, cuts, _forced = auto_split_ranges([(0, height)], energy, WIDTH, target_height=2000)
    assert cuts == [2100]
    assert ranges == [(0, 2100), (2100, height)]


def test_min_segment_respected():
    height = 4000
    energy = flat_energy(height)
    _, cuts, _forced = auto_split_ranges([(0, height)], energy, WIDTH, min_segment=520)
    for cut in cuts:
        assert cut >= 520
        assert height - cut >= 520


# ---------------------------------------------------------------------------
# rescue_gaps
# ---------------------------------------------------------------------------

def test_content_gap_attaches_to_following_panel():
    raw = flat_energy(3000, 1.0)
    raw[1050:1150] = 40.0  # caption text inside the gap between panels
    ranges = [(0, 1000), (1300, 2000)]
    out, rescued = rescue_gaps(ranges, raw)
    assert out == [(0, 1000), (1000, 2000)]
    assert rescued == ["y1000-1300->panel2"]


def test_empty_gap_stays_dropped():
    raw = flat_energy(3000, 1.0)
    ranges = [(0, 1000), (1300, 2000)]
    out, rescued = rescue_gaps(ranges, raw)
    assert out == ranges
    assert rescued == []


def test_huge_gap_not_rescued_even_with_content():
    raw = flat_energy(6000, 40.0)  # everything looks busy
    ranges = [(0, 1000), (3000, 4000)]  # 2000-px gap > max_gap
    out, rescued = rescue_gaps(ranges, raw)
    assert out == ranges
    assert rescued == []


def test_leading_gap_before_first_panel_is_rescued():
    raw = flat_energy(3000, 1.0)
    raw[100:200] = 40.0
    ranges = [(300, 1000)]
    out, rescued = rescue_gaps(ranges, raw)
    assert out == [(0, 1000)]
    assert rescued == ["y0-300->panel1"]


# ---------------------------------------------------------------------------
# apply_range_overrides
# ---------------------------------------------------------------------------

def test_no_overrides_is_identity():
    ranges = [(0, 100), (120, 300)]
    assert apply_range_overrides(ranges, None, 300) == ranges
    assert apply_range_overrides(ranges, {}, 300) == ranges


def test_replace_discards_detection():
    out = apply_range_overrides([(0, 100)], {"replace": [[0, 50], [60, 200]]}, 200)
    assert out == [(0, 50), (60, 200)]


def test_merge_joins_inclusive_index_range():
    ranges = [(0, 100), (120, 300), (320, 500)]
    out = apply_range_overrides(ranges, {"merge": [[0, 1]]}, 500)
    assert out == [(0, 300), (320, 500)]


def test_split_at_cuts_containing_range():
    ranges = [(0, 400)]
    out = apply_range_overrides(ranges, {"split_at": [250]}, 400)
    assert out == [(0, 250), (250, 400)]


def test_overrides_clamp_to_strip_and_drop_slivers():
    out = apply_range_overrides([(0, 100)], {"replace": [[-50, 100], [110, 125], [90, 500]]}, 300)
    # [-50,100] clamps to [0,100]; [110,125] is under min_height and dropped;
    # [90,500] clamps to [90,300]
    assert out == [(0, 100), (90, 300)]


# ---------------------------------------------------------------------------
# find_content_gaps
# ---------------------------------------------------------------------------

def test_trailing_content_drop_reported():
    raw = flat_energy(3000, 1.0)
    raw[2500:2900] = 90.0  # e.g. scanlator notice after the last panel
    drops = find_content_gaps([(0, 2400)], raw, 3000)
    assert len(drops) == 1
    assert drops[0].startswith("y2400-3000")


def test_quiet_edges_not_reported():
    raw = flat_energy(3000, 1.0)
    assert find_content_gaps([(100, 2900)], raw, 3000) == []


# ---------------------------------------------------------------------------
# band_energy + forced-cut flagging (the bubble-slice guard)
# ---------------------------------------------------------------------------

def test_band_energy_masks_single_quiet_rows():
    raw = flat_energy(2000, 50.0)
    raw[1000] = 0.0  # one quiet row (a bubble interior), no quiet band
    band = band_energy(raw)
    assert band[1000] == 50.0  # neighbours dominate: not a safe cut row


def test_band_energy_preserves_true_gutters():
    raw = flat_energy(2000, 50.0)
    raw[900:1100] = 0.0  # a 200-row real gutter
    band = band_energy(raw)
    assert band[1000] == 0.0


def test_forced_cut_flagged_when_no_gutter_band_exists():
    height = 4000
    raw = flat_energy(height, 50.0)
    raw[2100] = 0.0  # bubble-interior row: quiet alone, loud as a band
    band = band_energy(raw)
    ranges, cuts, forced = auto_split_ranges(
        [(0, height)], band, WIDTH, target_height=2000)
    assert len(cuts) == 1
    assert len(forced) == 1 and forced[0].startswith("y=")


def test_no_forced_flag_when_cut_lands_in_real_gutter():
    height = 4000
    raw = flat_energy(height, 50.0)
    raw[2050:2200] = 0.0  # a genuine gutter near the midpoint
    band = band_energy(raw)
    ranges, cuts, forced = auto_split_ranges(
        [(0, height)], band, WIDTH, target_height=2000)
    assert forced == []
    assert 2050 <= cuts[0] <= 2200
