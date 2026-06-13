"""mangaeasy.audio.normalize_chapter

Two-pass EBU R 128 / YouTube loudness normalization for a single chapter video.

Target: −14 LUFS integrated, −1.5 dBTP true peak, LRA 11 (YouTube standard).
The input file is replaced in place (the original is kept as *.before_normalize.mp4).

The command picks the best available chapter output in priority order:
  1. {chapter_dir}/{ch:02d}_{name}_with_bgm.mp4  (produced by add-bgm)
  2. {chapter_dir}/{ch:02d}_{name}.mp4            (produced by render-video)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from mangaeasy.config import load_download_config
from mangaeasy.paths import chapter_dir as _chapter_dir, output_video


TARGET_I   = -14.0
TARGET_TP  = -1.5
TARGET_LRA = 11.0
AUDIO_BITRATE = "192k"
SAMPLE_RATE   = 48000


def _run(cmd: list[str], capture: bool = False) -> subprocess.CompletedProcess[str]:
    print(" ".join(cmd), flush=True)
    return subprocess.run(
        cmd, check=True, capture_output=capture, text=True,
        encoding="utf-8", errors="replace",
    )


def _find_input(name: str, chapter: int) -> Path:
    ch_dir = _chapter_dir(name, chapter)
    with_bgm = ch_dir / f"{chapter:02d}_{name}_with_bgm.mp4"
    if with_bgm.exists():
        return with_bgm
    plain = output_video(name, chapter)
    if plain.exists():
        return plain
    raise FileNotFoundError(
        f"No chapter video found.\n"
        f"  Expected: {with_bgm}\n"
        f"       or: {plain}\n"
        f"Run `mangaeasy render-video` (and optionally `mangaeasy add-bgm`) first."
    )


def _loudnorm_base() -> str:
    return f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}"


def _parse_json(stderr: str) -> dict[str, str]:
    matches = re.findall(r"\{\s*\"input_i\".*?\}", stderr, flags=re.DOTALL)
    if not matches:
        raise ValueError("loudnorm JSON not found in ffmpeg output")
    data = json.loads(matches[-1])
    keys = ["input_i", "input_tp", "input_lra", "input_thresh", "target_offset"]
    return {k: str(data[k]) for k in keys}


def normalize(input_path: Path) -> None:
    tmp_path = input_path.with_name(f"{input_path.stem}.normalizing.tmp.mp4")
    backup   = input_path.with_name(f"{input_path.stem}.before_normalize.mp4")

    print(
        f"\nNormalizing: {input_path.name}\n"
        f"  Target: {TARGET_I} LUFS  /  {TARGET_TP} dBTP  /  LRA {TARGET_LRA}\n"
        f"  Pass 1 — measuring loudness …",
        flush=True,
    )

    # Pass 1 — measure
    result = _run(
        ["ffmpeg", "-hide_banner", "-nostats",
         "-i", str(input_path),
         "-map", "0:a:0",
         "-af", f"{_loudnorm_base()}:print_format=json",
         "-f", "null", "-"],
        capture=True,
    )
    measured = _parse_json(result.stderr)
    print(f"  Measured: {measured}", flush=True)
    print("  Pass 2 — encoding …", flush=True)

    # Pass 2 — encode
    af = (
        f"{_loudnorm_base()}:"
        f"measured_I={measured['input_i']}:"
        f"measured_TP={measured['input_tp']}:"
        f"measured_LRA={measured['input_lra']}:"
        f"measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}:"
        f"linear=true:print_format=summary,"
        f"aresample={SAMPLE_RATE}:async=1:first_pts=0"
    )
    _run([
        "ffmpeg", "-hide_banner", "-y",
        "-i", str(input_path),
        "-map", "0:v:0", "-map", "0:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", AUDIO_BITRATE,
        "-af", af,
        "-movflags", "+faststart",
        str(tmp_path),
    ])

    if backup.exists():
        backup.unlink()
    input_path.replace(backup)
    tmp_path.replace(input_path)
    print(f"\n[DONE] Normalized: {input_path.name}", flush=True)
    print(f"       Backup:     {backup.name}", flush=True)


def main() -> int:
    dl      = load_download_config()
    name    = str(dl["name"])
    chapter = int(dl["chapter"])

    try:
        video = _find_input(name, chapter)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    try:
        normalize(video)
    except Exception as exc:
        print(f"[ERROR] Normalization failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
