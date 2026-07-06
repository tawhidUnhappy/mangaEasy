"""Pure-logic tests for automatic BGM QC (mangaeasy.video_pipeline.music_bed).

Envelope arrays are synthesized directly (one value per 20 ms window), so no
audio files or ffmpeg are involved. The scenarios mirror the production
incident: short splice holes must be caught, musical rests and ending fades
must not be mistaken for defects.
"""

import numpy as np

from mangaeasy.video_pipeline.music_bed import (
    WIN_S,
    find_holes,
    find_trims,
    plan_keep_segments,
    plan_repeats,
    rms_envelope_db,
)


def env(seconds, level=-10.0):
    return np.full(int(seconds / WIN_S), level, dtype=np.float32)


def at(e, start_s, end_s, level):
    e[int(start_s / WIN_S):int(end_s / WIN_S)] = level
    return e


# ---------------------------------------------------------------------------
# find_holes
# ---------------------------------------------------------------------------

def test_short_deep_hole_is_detected():
    e = at(env(60), 30.0, 30.08, -60.0)  # 80 ms collapse in loud music
    holes = find_holes(e)
    assert len(holes) == 1
    h0, h1 = holes[0]
    assert 29.8 < h0 < 30.05 and 30.05 < h1 < 30.3


def test_musical_rest_in_quiet_passage_is_not_a_hole():
    e = env(60)
    at(e, 20.0, 25.0, -24.0)   # quiet passage
    at(e, 22.0, 22.3, -44.0)   # rest inside it (like the track's phrase gaps)
    assert find_holes(e) == []


def test_long_quiet_stretch_is_not_a_hole():
    e = at(env(60), 30.0, 31.0, -60.0)  # a full second of near-silence
    assert find_holes(e) == []


def test_ending_fade_is_not_a_hole():
    e = env(60)
    e[-int(3 / WIN_S):] = -70.0  # music stops and never recovers
    assert find_holes(e) == []


def test_two_holes_both_found():
    e = env(120)
    at(e, 40.0, 40.08, -55.0)
    at(e, 80.0, 80.1, -58.0)
    assert len(find_holes(e)) == 2


# ---------------------------------------------------------------------------
# find_trims / rms_envelope_db
# ---------------------------------------------------------------------------

def test_trims_detect_silent_lead_and_tail():
    e = env(30)
    at(e, 0.0, 1.2, -80.0)
    at(e, 28.5, 30.0, -80.0)
    lead, tail = find_trims(e)
    assert 1.0 < lead < 1.4
    assert 28.3 < tail < 28.7


def test_envelope_of_silence_is_low_and_of_tone_is_high():
    sr = 8000
    silent = rms_envelope_db(np.zeros(sr, dtype=np.float32), sr)
    loud = rms_envelope_db(np.ones(sr, dtype=np.float32) * 0.5, sr)
    assert silent.max() < -100
    assert loud.min() > -10


# ---------------------------------------------------------------------------
# plan_keep_segments / plan_repeats
# ---------------------------------------------------------------------------

def test_keep_segments_cut_around_holes_with_margin():
    segments = plan_keep_segments(185.0, [(84.2, 84.3)], 1.2, 184.4, margin_s=0.1)
    assert [(round(a, 3), round(b, 3)) for a, b in segments] == [(1.2, 84.1), (84.4, 184.4)]


def test_keep_segments_drop_slivers():
    segments = plan_keep_segments(100.0, [(10.0, 10.1), (10.5, 10.6)], 0.0, 100.0,
                                  margin_s=0.1, min_segment_s=1.0)
    # the 0.3 s scrap between the two holes is discarded
    assert [(round(a, 3), round(b, 3)) for a, b in segments] == [(0.0, 9.9), (10.7, 100.0)]


def test_plan_repeats_covers_target():
    assert plan_repeats(100.0, 98.0) == 1          # already long enough
    n = plan_repeats(164.7, 3600.0, xfade_s=3.0)
    covered = 164.7 + (n - 1) * (164.7 - 3.0)
    assert covered >= 3600.0
    assert plan_repeats(164.7, 3600.0, xfade_s=3.0) == n


def test_plan_repeats_degenerate_core():
    assert plan_repeats(2.0, 100.0, xfade_s=3.0) == 1  # core shorter than xfade: no loop
