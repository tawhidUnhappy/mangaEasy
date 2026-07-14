"""validate_items_strict must check audio under <audio-root>/<project>/<item>.

Regression: the project name was computed into `name` and then shadowed by
the item loop variable, so the audio check silently looked in
<audio-root>/<item>/<item>/ — a directory that never exists — and every real
build stopped with "missing audio" for WAVs that were all present (hit in
production on IGetStrongerByDoingNothing, 2026-07-14).
"""

import json

import pytest

from mangaeasy.video_pipeline.long_video_builder import LongVideoConfig, validate_items_strict


def make_project(tmp_path, items=("01", "02")):
    project_root = tmp_path / "library" / "MyProject"
    audio_root = tmp_path / "audio"
    for item in items:
        panels = project_root / item / "panels"
        panels.mkdir(parents=True)
        entries = []
        for n in range(2):
            stem = f"ch{item}_{n:03d}"
            (panels / f"{stem}.png").write_bytes(b"png")
            (audio_root / "MyProject" / item / f"{stem}.wav").parent.mkdir(
                parents=True, exist_ok=True)
            (audio_root / "MyProject" / item / f"{stem}.wav").write_bytes(b"wav")
            entries.append({"image": f"{stem}.png", "narration": f"line {n}"})
        (project_root / item / "narration.json").write_text(
            json.dumps(entries), encoding="utf-8")
    config = LongVideoConfig(
        project_root=project_root,
        output_root=tmp_path / "output",
        work_dir=tmp_path / "work",
        audio_root=audio_root,
    )
    chapters = {item: tmp_path / f"item_{item}.mp4" for item in items}
    return config, chapters, audio_root


def test_complete_audio_passes(tmp_path):
    config, chapters, _ = make_project(tmp_path)
    # Must not raise: every WAV exists under <audio-root>/<project>/<item>/.
    validate_items_strict(config, chapters, ["01", "02"])


def test_genuinely_missing_audio_still_fails(tmp_path):
    config, chapters, audio_root = make_project(tmp_path)
    (audio_root / "MyProject" / "02" / "ch02_001.wav").unlink()
    with pytest.raises(FileNotFoundError, match=r"item 02: missing audio for ch02_001"):
        validate_items_strict(config, chapters, ["01", "02"])
