"""Agent-style end-to-end smoke: drive a tiny fixture project through
video-check → video-render over plain pipes (no PTY, no ML models), then
find the output via the MANGAEASY_RESULT marker — exactly how an AI
assistant is documented to use the CLI in docs/ai-guide.md."""

import json
import os
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest

# Importing the CLI puts any vendored ffmpeg/ffprobe onto this process'
# PATH (ensure_vendored_path), which the subprocesses below inherit.
import mangaeasy.cli  # noqa: F401

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)

PANELS = ["p001", "p002"]


def write_png(path: Path, size=(320, 480)) -> None:
    from PIL import Image

    Image.new("RGB", size, color=(120, 40, 200)).save(path)


def write_silent_wav(path: Path, seconds: float = 0.3, rate: int = 48000) -> None:
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(struct.pack("<h", 0) * int(seconds * rate))


@pytest.fixture()
def fixture_project(tmp_path: Path) -> dict:
    proj = tmp_path / "library" / "tinyproj"
    panels = proj / "01" / "panels"
    panels.mkdir(parents=True)
    for stem in PANELS:
        write_png(panels / f"{stem}.png")
    (proj / "01" / "narration.json").write_text(
        json.dumps([{"image": f"{stem}.png", "narration": f"line {i}"} for i, stem in enumerate(PANELS)]),
        encoding="utf-8",
    )
    audio_root = tmp_path / "audio"
    audio_dir = audio_root / "tinyproj" / "01"
    audio_dir.mkdir(parents=True)
    for stem in PANELS:
        write_silent_wav(audio_dir / f"{stem}.wav")
    return {
        "project_root": proj,
        "audio_root": audio_root,
        "output_root": tmp_path / "output",
        "work_dir": tmp_path / "work",
    }


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "mangaeasy.cli", *args],
        capture_output=True, text=True, encoding="utf-8", timeout=600, env=os.environ.copy(),
    )


def test_check_then_render_end_to_end(fixture_project):
    check = run_cli(
        "video-check",
        "--project-root", str(fixture_project["project_root"]),
        "--audio-root", str(fixture_project["audio_root"]),
        "--json",
    )
    assert check.returncode == 0, check.stdout + check.stderr
    report = json.loads(check.stdout.strip().splitlines()[-1])
    assert report["ok"] is True
    assert report["items"][0]["panels"] == len(PANELS)

    render = run_cli(
        "video-render",
        "--project-root", str(fixture_project["project_root"]),
        "--audio-root", str(fixture_project["audio_root"]),
        "--output-root", str(fixture_project["output_root"]),
        "--work-dir", str(fixture_project["work_dir"]),
        "--items", "01",
        "--width", "640", "--height", "360", "--fps", "10",
        "--encoder", "auto", "--workers", "1",
    )
    assert render.returncode == 0, render.stdout[-3000:] + render.stderr[-3000:]

    result_lines = [ln for ln in render.stdout.splitlines() if ln.startswith("MANGAEASY_RESULT ")]
    assert result_lines, "video-render must end with a MANGAEASY_RESULT line"
    payload = json.loads(result_lines[-1][len("MANGAEASY_RESULT "):])
    assert payload["outputs"], payload
    out_file = Path(payload["outputs"][0])
    assert out_file.exists() and out_file.stat().st_size > 0
    assert out_file.name == "item_01.mp4"
