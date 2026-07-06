"""Automatic background-music QC and seamless-bed preparation.

`video-add-bgm` used to loop the given music file raw (`-stream_loop -1`,
no crossfade), which meant any defect in the track repeated through the whole
long video. Production incident (2026-07-06, documented in
docs/recap-video-playbook.md): a YouTube-ripped WAV contained two ~80 ms
splice holes mid-phrase plus a leading-silence intro and an ending fade — the
published video had audible "music cuts out and restarts" moments at 1:24 and
2:15 and a ~0.5 s dead zone at every track-end loop seam, and had to be
replaced. `silencedetect` cannot catch such holes (they are shorter than its
duration window), so this module scans a 20 ms RMS envelope instead.

`prepare_music_bed()` is the entry point. Given the music file and the video
duration it:

1. analyzes the track's envelope (decoded at 8 kHz mono — analysis only,
   rendering always uses the original samples);
2. detects splice holes: brief (< 0.5 s) collapses of 25+ dB below the recent
   level while the music is otherwise loud — musical rests and quiet passages
   don't qualify because the surrounding level is already low;
3. trims leading/trailing silence and the ending fade;
4. renders a repaired core (defects cut out, ~0.35 s equal-power crossfades)
   and, when the video is longer than the core, chains copies with 3 s
   crossfades into a seamless bed at least as long as the video;
5. caches the bed under `<work-dir>/music_bed/` keyed by the source file's
   identity and the target-length bucket, so re-mixes are free.

If the track is clean, long enough, and needs no trimming, the original file
is returned untouched. Any analysis/render failure falls back to the original
file with a warning — bed preparation must never break the mix itself.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import List, Tuple

import numpy as np

from mangaeasy.video_pipeline.audio_audit import ffprobe_duration

Segment = Tuple[float, float]

ANALYSIS_SR = 8000
WIN_S = 0.02                 # envelope window (s) — short enough to see 80 ms holes
HOLE_DROP_DB = 25.0          # window this far below the recent median = candidate hole
HOLE_CONTEXT_DB = -25.0      # only while the surrounding music is at least this loud
HOLE_MAX_S = 0.5             # longer quiet stretches are musical, not defects
SILENCE_DB = -45.0           # lead/tail trim threshold
XFADE_REPAIR_S = 0.35        # crossfade across a removed hole
XFADE_LOOP_S = 3.0           # crossfade between looped copies
HOLE_MARGIN_S = 0.12         # extra cut margin around a detected hole
TARGET_BUCKET_S = 300.0      # cache granularity for the bed length
MIN_CORE_S = 20.0            # below this, looping would sound absurd — use raw file
PARAMS_VERSION = 1           # bump to invalidate cached beds when logic changes


# ---------------------------------------------------------------------------
# Analysis (pure logic on an envelope array — unit-testable without ffmpeg)
# ---------------------------------------------------------------------------

def rms_envelope_db(samples: np.ndarray, sample_rate: int, win_s: float = WIN_S) -> np.ndarray:
    """Per-window RMS level in dBFS for a mono float32 signal."""
    n = max(1, int(sample_rate * win_s))
    m = len(samples) // n
    if m == 0:
        return np.full(1, -180.0, dtype=np.float32)
    rms = np.sqrt((samples[:m * n].reshape(m, n).astype(np.float64) ** 2).mean(axis=1))
    return (20 * np.log10(rms + 1e-9)).astype(np.float32)


def find_holes(
    env_db: np.ndarray,
    *,
    win_s: float = WIN_S,
    drop_db: float = HOLE_DROP_DB,
    context_db: float = HOLE_CONTEXT_DB,
    max_hole_s: float = HOLE_MAX_S,
) -> List[Segment]:
    """Splice holes: short deep collapses while the music around them is loud.

    A window is a hole candidate when it sits `drop_db` below the median of
    the preceding half second AND that preceding level is above `context_db`.
    Candidates are grouped; groups longer than `max_hole_s` are rejected
    (that's a quiet passage, not a splice), as are groups where the music
    doesn't come back afterwards (an ending fade).
    """
    ctx = max(1, int(0.5 / win_s))
    holes: List[Segment] = []
    i = ctx
    n = len(env_db)
    while i < n:
        prev = float(np.median(env_db[i - ctx:i]))
        if env_db[i] < prev - drop_db and prev > context_db:
            j = i
            while j < n and env_db[j] < prev - drop_db * 0.6:
                j += 1
            # "Recovers" only needs to exclude ending fades (music never comes
            # back): any window in the next half second returning near level
            # counts — a median test misses holes followed by a quieter phrase.
            after = env_db[j:j + ctx]
            recovers = after.size > 0 and float(after.max()) > prev - 12.0
            if (j - i) * win_s <= max_hole_s and recovers:
                holes.append((i * win_s, j * win_s))
            i = j + 1
        else:
            i += 1
    return holes


def find_trims(env_db: np.ndarray, *, win_s: float = WIN_S,
               silence_db: float = SILENCE_DB) -> Tuple[float, float]:
    """(lead_s, tail_s): first and last moments the track is actually audible."""
    audible = np.flatnonzero(env_db > silence_db)
    if audible.size == 0:
        return 0.0, len(env_db) * win_s
    return float(audible[0] * win_s), float((audible[-1] + 1) * win_s)


def plan_keep_segments(
    duration_s: float,
    holes: List[Segment],
    lead_s: float,
    tail_s: float,
    *,
    margin_s: float = HOLE_MARGIN_S,
    min_segment_s: float = 1.0,
) -> List[Segment]:
    """Audible span cut at each hole (with margin), holes and slivers dropped."""
    cuts = sorted((max(lead_s, h0 - margin_s), min(tail_s, h1 + margin_s)) for h0, h1 in holes)
    segments: List[Segment] = []
    cursor = lead_s
    for c0, c1 in cuts:
        if c0 > cursor:
            segments.append((cursor, c0))
        cursor = max(cursor, c1)
    if cursor < tail_s:
        segments.append((cursor, tail_s))
    return [(a, min(b, duration_s)) for a, b in segments if b - a >= min_segment_s]


def plan_repeats(core_s: float, target_s: float, xfade_s: float = XFADE_LOOP_S) -> int:
    """How many copies of the core, crossfade-chained, cover target_s (+2 s slack)."""
    if core_s >= target_s + 2:
        return 1
    effective = core_s - xfade_s
    if effective <= 0:
        return 1
    return 1 + math.ceil((target_s + 2 - core_s) / effective)


# ---------------------------------------------------------------------------
# Rendering (ffmpeg; original samples, never the 8 kHz analysis copy)
# ---------------------------------------------------------------------------

def _decode_for_analysis(music: Path) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(music),
         "-map", "a:0", "-ac", "1", "-ar", str(ANALYSIS_SR), "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


def _render_core(music: Path, segments: List[Segment], out: Path,
                 xfade_s: float = XFADE_REPAIR_S) -> None:
    parts = []
    for k, (a, b) in enumerate(segments):
        parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=N/SR/TB[s{k}]")
    graph = ";".join(parts)
    last = "[s0]"
    for k in range(1, len(segments)):
        nxt = f"[j{k}]" if k < len(segments) - 1 else "[core]"
        graph += f";{last}[s{k}]acrossfade=d={xfade_s}{nxt}"
        last = nxt
    if len(segments) == 1:
        graph += ";[s0]anull[core]"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(music),
         "-filter_complex", graph, "-map", "[core]",
         "-ar", "48000", "-ac", "2", "-c:a", "flac", str(out)],
        check=True, capture_output=True,
    )


def _render_loop(core: Path, repeats: int, out: Path, xfade_s: float = XFADE_LOOP_S) -> None:
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for _ in range(repeats):
        cmd += ["-i", str(core)]
    last = "[0:a]"
    graph = ""
    for k in range(1, repeats):
        nxt = f"[x{k}]" if k < repeats - 1 else "[bed]"
        graph += f"{last}[{k}:a]acrossfade=d={xfade_s}{nxt};"
        last = nxt
    graph = graph.rstrip(";")
    cmd += ["-filter_complex", graph, "-map", "[bed]", "-c:a", "flac", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)


def _cache_key(music: Path, bed_target_s: float) -> str:
    stat = music.stat()
    ident = f"{music.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{bed_target_s:.0f}|v{PARAMS_VERSION}"
    return hashlib.sha1(ident.encode("utf-8")).hexdigest()[:16]


def prepare_music_bed(music: Path, video_duration_s: float, work_dir: Path) -> Tuple[Path, dict]:
    """Return (music file to mix with, report). Falls back to the input on any failure."""
    report: dict = {"source": str(music), "used_bed": False}
    try:
        duration = ffprobe_duration(music)
        if duration is None:
            report["note"] = "could not probe music duration; using file as-is"
            return music, report

        samples = _decode_for_analysis(music)
        env = rms_envelope_db(samples, ANALYSIS_SR)
        holes = find_holes(env)
        lead, tail = find_trims(env)
        report.update({
            "holes": [[round(a, 2), round(b, 2)] for a, b in holes],
            "lead_trim_s": round(lead, 2),
            "tail_trim_s": round(duration - tail, 2),
        })

        needs_loop = duration < video_duration_s
        needs_repair = bool(holes)
        needs_trim = needs_loop and (lead > 0.5 or duration - tail > 0.5)
        if not (needs_repair or needs_trim or needs_loop):
            return music, report

        segments = plan_keep_segments(duration, holes, lead, tail)
        core_s = sum(b - a for a, b in segments) - XFADE_REPAIR_S * max(0, len(segments) - 1)
        if core_s < MIN_CORE_S:
            report["note"] = f"usable music too short to loop ({core_s:.1f}s); using file as-is"
            return music, report

        bed_target_s = math.ceil(max(video_duration_s + 5, core_s) / TARGET_BUCKET_S) * TARGET_BUCKET_S
        bed_dir = work_dir / "music_bed"
        bed_dir.mkdir(parents=True, exist_ok=True)
        key = _cache_key(music, bed_target_s)
        bed = bed_dir / f"bed_{key}.flac"
        meta = bed_dir / f"bed_{key}.json"

        if bed.is_file() and (ffprobe_duration(bed) or 0) >= video_duration_s:
            report.update({"used_bed": True, "bed": str(bed), "cached": True})
            return bed, report

        core = bed_dir / f"core_{key}.flac"
        _render_core(music, segments, core)
        repeats = plan_repeats(core_s, bed_target_s)
        if repeats == 1:
            core.replace(bed)
        else:
            _render_loop(core, repeats, bed)
            core.unlink(missing_ok=True)

        report.update({
            "used_bed": True, "bed": str(bed), "cached": False,
            "segments": [[round(a, 2), round(b, 2)] for a, b in segments],
            "core_s": round(core_s, 1), "repeats": repeats,
            "bed_s": round(ffprobe_duration(bed) or 0.0, 1),
        })
        meta.write_text(json.dumps(report, indent=1), encoding="utf-8")
        return bed, report
    except Exception as exc:  # never break the mix over bed preparation
        report["note"] = f"music bed preparation failed ({exc}); using file as-is"
        return music, report


def describe_report(report: dict) -> str:
    """One human line for the add-bgm log."""
    if not report.get("used_bed"):
        note = report.get("note")
        holes = report.get("holes") or []
        if note:
            return f"[music-bed] {note}"
        if holes:
            return f"[music-bed] WARNING: holes detected at {holes} but no bed was built"
        return "[music-bed] music file is clean and long enough; mixing it as-is"
    if report.get("cached"):
        return f"[music-bed] reusing cached seamless bed: {report['bed']}"
    holes = report.get("holes") or []
    fixed = f"repaired {len(holes)} splice hole(s) at {holes}, " if holes else ""
    return (f"[music-bed] {fixed}trimmed lead {report.get('lead_trim_s', 0)}s / "
            f"tail {report.get('tail_trim_s', 0)}s, core {report.get('core_s')}s x "
            f"{report.get('repeats')} -> seamless bed {report.get('bed_s')}s: {report['bed']}")
