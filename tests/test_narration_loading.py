"""`load_narration` is the single source of truth for reading an item's
narration — including the `intro.json` prepend behaviour that has bitten
modules re-implementing their own loader in the past."""

import json
import sys

import pytest

from mediaconductor.video_pipeline import generate_audio_indextts
from mediaconductor.video_pipeline.item_assets import (
    frame_aligned_duration,
    load_narration,
    validate_calm_narration,
)


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_reads_narration_json(tmp_path):
    write_json(tmp_path / "narration.json", [{"image": "a.png", "narration": "Hello"}])
    assert load_narration(tmp_path) == [{"image": "a.png", "narration": "Hello"}]


def test_intro_json_is_prepended(tmp_path):
    write_json(tmp_path / "narration.json", [{"image": "a.png", "narration": "main"}])
    write_json(tmp_path / "intro.json", [{"image": "hook.png", "narration": "cold open"}])
    entries = load_narration(tmp_path)
    assert [e["image"] for e in entries] == ["hook.png", "a.png"]


def test_utf8_bom_tolerated(tmp_path):
    (tmp_path / "narration.json").write_bytes(b"\xef\xbb\xbf" + json.dumps([{"image": "a.png"}]).encode())
    assert load_narration(tmp_path) == [{"image": "a.png"}]


def test_non_array_narration_rejected(tmp_path):
    write_json(tmp_path / "narration.json", {"image": "a.png"})
    with pytest.raises(ValueError):
        load_narration(tmp_path)


def test_non_array_intro_rejected(tmp_path):
    write_json(tmp_path / "narration.json", [])
    write_json(tmp_path / "intro.json", {"image": "a.png"})
    with pytest.raises(ValueError):
        load_narration(tmp_path)


def test_calm_preflight_blocks_delivery_and_emotion_before_tts(tmp_path):
    entries = [
        {"image": "a.png", "narration": "GHAHA!", "emotion": "excited"},
    ]
    with pytest.raises(ValueError, match="calm-narration policy") as exc:
        validate_calm_narration(entries, tmp_path / "narration.json")
    assert "a.png" in str(exc.value)


def test_calm_preflight_accepts_restrained_narration(tmp_path):
    entries = [
        {"image": "a.png", "narration": "The phoenix appears.", "emotion": "calm"},
        {"image": "b.png", "narration": "NASA records the event."},
    ]
    validate_calm_narration(entries, tmp_path / "narration.json")


def test_indextts_outer_preflight_blocks_all_workers_before_start(tmp_path, monkeypatch):
    project_root = tmp_path / "Story"
    for name, narration in (
        ("01", "The first chapter begins calmly."),
        ("02", "GHAHA! The second chapter begins."),
    ):
        item_dir = project_root / name
        item_dir.mkdir(parents=True)
        write_json(item_dir / "narration.json", [{"image": f"{name}_001.png", "narration": narration}])
    speaker = tmp_path / "speaker.wav"
    speaker.write_bytes(b"placeholder")
    subprocesses: list[list[str]] = []
    monkeypatch.setattr(generate_audio_indextts.runtime, "run", lambda command, **_kwargs: subprocesses.append(command))
    monkeypatch.setattr(sys, "argv", [
        "video-audio-indextts",
        "--project-root", str(project_root),
        "--speaker-wav", str(speaker),
        "--gpu-workers", "2",
        "--item-range", "01-02",
    ])
    with pytest.raises(ValueError, match="calm-narration policy"):
        generate_audio_indextts.main()
    assert subprocesses == []


def test_frame_aligned_duration_rounds_up_to_whole_frames():
    # 1.01 s at 30 fps -> 31 frames, never truncating audio
    duration, frames = frame_aligned_duration(1.01, 30)
    assert frames == 31
    assert duration == pytest.approx(31 / 30)


def test_frame_aligned_duration_minimum_one_frame():
    duration, frames = frame_aligned_duration(0.0, 30)
    assert frames == 1
    assert duration == pytest.approx(1 / 30)
