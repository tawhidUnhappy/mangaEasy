"""mangaeasy.tools.smoke — prove a fresh install actually works end to end.

``mangaeasy smoke-test`` builds a tiny throwaway project (two generated
panels + narration), runs it through the real video pipeline (audio →
render → probe) and checks the output MP4's streams and duration. It is the
last step of the from-clone setup runbook: ``doctor --json`` says the parts
are installed; this proves they work together.

- Default audio is silent WAVs synthesized with ffmpeg (fast, no models) —
  it exercises ffmpeg, encoder autodetection and the whole render path.
- ``--tts kokoro`` additionally runs the real Kokoro TTS on the fixture
  narration (needs ``install-tool kokoro-82m``; first run downloads the
  model), proving the TTS toolchain too.

Everything happens under ``<work-dir>/smoke_test/`` and is deleted on
success (``--keep`` to inspect). Exit 0 = the machine can produce videos.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from mangaeasy.brand import CLI_NAME
from mangaeasy.runtime import cli_command
from mangaeasy.utils import emit_result

PANEL_SECONDS = 1.2
NARRATION = [
    {"image": "smoke_001.png", "narration": "The smoke test begins."},
    {"image": "smoke_002.png", "narration": "And the pipeline holds."},
]


def build_fixture_project(base: Path) -> Path:
    """Create a minimal library/SmokeTest project; returns the project root."""
    from PIL import Image, ImageDraw

    item_dir = base / "library" / "SmokeTest" / "01"
    panels = item_dir / "panels"
    panels.mkdir(parents=True, exist_ok=True)
    for i, color in enumerate(((70, 130, 220), (220, 130, 70)), 1):
        img = Image.new("RGB", (1000, 1200), color)
        draw = ImageDraw.Draw(img)
        draw.ellipse([250, 350, 750, 850], fill=(245, 245, 245), outline=(0, 0, 0), width=8)
        draw.text((430, 570), f"PANEL {i}", fill=(0, 0, 0))
        img.save(panels / f"smoke_{i:03d}.png")
    (item_dir / "narration.json").write_text(
        json.dumps(NARRATION, indent=1) + "\n", encoding="utf-8")
    return base / "library" / "SmokeTest"


def synth_silent_wavs(audio_item_dir: Path) -> None:
    audio_item_dir.mkdir(parents=True, exist_ok=True)
    for entry in NARRATION:
        stem = Path(entry["image"]).stem
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
             "-t", str(PANEL_SECONDS), str(audio_item_dir / f"{stem}.wav")],
            check=True,
        )


def probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,codec_name",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout or "{}")


def parse_args() -> argparse.Namespace:
    from mangaeasy.video_pipeline.common import DEFAULT_WORK_DIR

    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} smoke-test",
        description="Build and verify a tiny real video to prove the install works "
                    f"(run after `{CLI_NAME} setup`).",
    )
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--tts", choices=("silent", "kokoro"), default="silent",
                        help="silent = ffmpeg-synthesized WAVs (default, no models); "
                             "kokoro = real TTS via the kokoro-82m tool env.")
    parser.add_argument("--keep", action="store_true",
                        help="Keep <work-dir>/smoke_test/ for inspection.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base = args.work_dir.resolve() / "smoke_test"
    if base.exists():
        shutil.rmtree(base)
    checks: dict[str, str] = {}

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print(f"FAIL: ffmpeg/ffprobe not found — run `{CLI_NAME} setup` "
              f"(or `{CLI_NAME} bootstrap-tools`) first")
        return 1
    checks["ffmpeg"] = "ok"

    project_root = build_fixture_project(base)
    checks["fixture"] = "ok"
    audio_root = base / "audio"
    output_root = base / "output"

    if args.tts == "kokoro":
        result = subprocess.run(cli_command(
            "video-audio",
            "--project-root", str(project_root),
            "--audio-root", str(audio_root),
            "--items", "01", "--gpu-workers", "1",
        ))
        if result.returncode != 0:
            print("FAIL: Kokoro TTS generation failed — check "
                  f"`{CLI_NAME} install-tool kokoro-82m` / `{CLI_NAME} doctor --json`")
            emit_result(command="smoke-test", ok=False, checks=checks)
            return 1
        checks["tts"] = "kokoro"
    else:
        synth_silent_wavs(audio_root / "SmokeTest" / "01")
        checks["tts"] = "silent"

    result = subprocess.run(cli_command(
        "video-render",
        "--project-root", str(project_root),
        "--audio-root", str(audio_root),
        "--output-root", str(output_root),
        "--work-dir", str(base / "work"),
        "--items", "01", "--workers", "1",
    ))
    if result.returncode != 0:
        print("FAIL: video render failed — see the ffmpeg output above")
        emit_result(command="smoke-test", ok=False, checks=checks)
        return 1

    video = output_root / "SmokeTest" / "items" / "item_01.mp4"
    if not video.is_file():
        print(f"FAIL: expected output missing: {video}")
        emit_result(command="smoke-test", ok=False, checks=checks)
        return 1
    data = probe(video)
    codecs = {s.get("codec_type"): s.get("codec_name") for s in data.get("streams", [])}
    duration = float(data.get("format", {}).get("duration") or 0.0)
    problems = []
    if codecs.get("video") != "h264":
        problems.append(f"video codec {codecs.get('video')!r} != h264")
    if codecs.get("audio") != "aac":
        problems.append(f"audio codec {codecs.get('audio')!r} != aac")
    if args.tts == "silent" and abs(duration - len(NARRATION) * PANEL_SECONDS) > 0.6:
        problems.append(f"duration {duration:.2f}s far from expected "
                        f"{len(NARRATION) * PANEL_SECONDS:.2f}s")
    if duration <= 0:
        problems.append("zero duration")
    if problems:
        for p in problems:
            print(f"FAIL: {p}")
        emit_result(command="smoke-test", ok=False, checks=checks, problems=problems)
        return 1
    checks["render"] = f"h264+aac {duration:.2f}s"

    print(f"SMOKE TEST PASS — {video.name}: h264+aac, {duration:.2f}s "
          f"(tts={checks['tts']})")
    if args.keep:
        print(f"[keep] artifacts left under {base}")
    else:
        shutil.rmtree(base, ignore_errors=True)
    emit_result(command="smoke-test", ok=True, checks=checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
