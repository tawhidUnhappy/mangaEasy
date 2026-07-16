"""Pure-logic tests for automatic BGM QC (mangaeasy.video_pipeline.music_bed).

Envelope arrays are synthesized directly (one value per 20 ms window), so no
audio files or ffmpeg are involved. The scenarios mirror the production
incident: short splice holes must be caught, musical rests and ending fades
must not be mistaken for defects.
"""

import numpy as np

from mangaeasy.video_pipeline.music_bed import (
    MAX_LOUDNORM_GAIN_DB,
    WIN_S,
    find_holes,
    find_trims,
    music_loudnorm_pregain,
    parse_ebur128_integrated,
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


# ---------------------------------------------------------------------------
# music loudness pre-normalization
# ---------------------------------------------------------------------------

EBUR128_TAIL = """\
[Parsed_ebur128_0 @ 000001] Summary:

  Integrated loudness:
    I:         -13.2 LUFS
    Threshold: -23.6 LUFS

  Loudness range:
    LRA:         7.9 LU
    Threshold: -33.5 LUFS
    LRA low:   -20.5 LUFS
    LRA high:  -12.6 LUFS
"""


def test_parse_ebur128_integrated_reads_summary():
    assert parse_ebur128_integrated(EBUR128_TAIL) == -13.2


def test_parse_ebur128_integrated_ignores_other_lufs_lines():
    # Threshold / LRA lines also end in LUFS but must not match.
    text = EBUR128_TAIL.replace("    I:         -13.2 LUFS\n", "")
    assert parse_ebur128_integrated(text) is None


def test_parse_ebur128_integrated_takes_last_match():
    assert parse_ebur128_integrated(EBUR128_TAIL + "\n  I: -10.0 LUFS\n") == -10.0


def test_pregain_aligns_to_reference():
    assert music_loudnorm_pregain(-13.2) == -0.8000000000000007  # −14 − (−13.2)
    assert music_loudnorm_pregain(-20.0) == 6.0


def test_pregain_accepts_measured_narration_reference():
    assert music_loudnorm_pregain(-20.0, reference_lufs=-18.0) == 2.0
    # The one-argument API remains the fixed -14 LUFS fallback.
    assert music_loudnorm_pregain(-20.0) == 6.0


def test_pregain_clamps_and_handles_missing():
    assert music_loudnorm_pregain(None) == 0.0
    assert music_loudnorm_pregain(-60.0) == MAX_LOUDNORM_GAIN_DB
    assert music_loudnorm_pregain(30.0) == -MAX_LOUDNORM_GAIN_DB


# ---------------------------------------------------------------------------
# bed conditioning filter
# ---------------------------------------------------------------------------

from mangaeasy.video_pipeline.music_bed import build_condition_filter  # noqa: E402


def test_condition_filter_compress_and_eq():
    f = build_condition_filter(compress=True, eq_carve=True)
    assert "acompressor=" in f and "equalizer=" in f
    assert "width_type=q" in f


def test_condition_filter_stages_toggle_independently():
    assert "equalizer=" not in build_condition_filter(compress=True, eq_carve=False)
    assert "acompressor=" not in build_condition_filter(compress=False, eq_carve=True)
    # both off => a no-op filter (caller returns the bed untouched)
    assert build_condition_filter(compress=False, eq_carve=False) == "anull"


# ---------------------------------------------------------------------------
# mix filter (regression guards for the two load-bearing amix/limiter bugs)
# ---------------------------------------------------------------------------

from mangaeasy.video_pipeline.add_long_video_bgm import build_mix_filter  # noqa: E402
from mangaeasy.video_pipeline.add_long_video_bgm import (  # noqa: E402
    DEFAULT_EXISTING_VIDEO_NARRATION_VOLUME,
)


def test_standalone_bgm_defaults_to_unity_narration_gain():
    assert DEFAULT_EXISTING_VIDEO_NARRATION_VOLUME == 1.0


def test_mix_filter_keeps_amix_and_limiter_invariants():
    # Both branches must never reintroduce amix's 1/inputs rescale or the
    # limiter's auto-level, which each silently undid the -14 LUFS target.
    for duck in (True, False):
        f = build_mix_filter(narration_volume=1.0, music_volume_db=-19.0, duck=duck)
        assert "normalize=0" in f
        assert "alimiter=level=disabled" in f
        assert "volume=-19.0dB" in f
        assert f.endswith("[a]")


def test_mix_filter_duck_toggles_sidechain():
    ducked = build_mix_filter(narration_volume=1.0, music_volume_db=-19.0, duck=True,
                              duck_ratio=2.0, duck_threshold=0.08)
    flat = build_mix_filter(narration_volume=1.0, music_volume_db=-19.0, duck=False)
    assert "sidechaincompress=" in ducked and "ratio=2.0" in ducked and "threshold=0.08" in ducked
    assert "sidechaincompress=" not in flat
