"""Mocked regression tests for fade-safe video pipeline orchestration."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mangaeasy import defaults
from mangaeasy.video_pipeline import run_pipeline


def _command_name(command: list[str]) -> str:
    names = (
        "video-fade-audio", "video-render", "video-join",
        "video-add-bgm", "video-normalize-audio", "video-audio",
    )
    return next(name for name in names if name in command)


def _flag_value(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


def _invoke(tmp_path: Path, monkeypatch, extra: list[str]):
    project_root = tmp_path / "library" / "Story"
    audio_root = tmp_path / "audio"
    output_root = tmp_path / "output"
    project_root.mkdir(parents=True)
    long_video = output_root / "Story" / "Story_full.mp4"
    long_video.parent.mkdir(parents=True)
    long_video.write_bytes(b"placeholder")
    commands: list[list[str]] = []
    monkeypatch.setattr(defaults, "SYSTEM_CONFIG_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(run_pipeline, "resolve_tts_engine", lambda *_args: "kokoro")
    monkeypatch.setattr(run_pipeline, "run", lambda command, _cwd: commands.append(list(command)))
    monkeypatch.setattr(run_pipeline, "find_latest_long_video", lambda *_args: long_video)
    monkeypatch.setattr(run_pipeline, "emit_result", lambda **_kwargs: None)
    monkeypatch.setattr(sys, "argv", [
        "video", "--project-root", str(project_root),
        "--audio-root", str(audio_root), "--output-root", str(output_root),
        "--work-dir", str(tmp_path / "work"), "--skip-audio", "--items", "01", *extra,
    ])
    assert run_pipeline.main() == 0
    return commands, long_video


@pytest.mark.parametrize(
    ("extra", "expected_fade_ms"),
    [
        (["--no-background-music"], "8.0"),
        (["--audio-source", "faded", "--audio-fade-ms", "12.5",
          "--no-background-music"], "12.5"),
    ],
)
def test_faded_audio_precedes_render_and_uses_sibling_root(
        tmp_path, monkeypatch, extra, expected_fade_ms):
    commands, _long_video = _invoke(tmp_path, monkeypatch, extra)
    assert [_command_name(command) for command in commands] == [
        "video-fade-audio", "video-render",
    ]
    fade, render = commands
    raw_root = (tmp_path / "audio").resolve()
    faded_root = raw_root.with_name("audio_faded")
    assert Path(_flag_value(fade, "--source-audio-root")).resolve() == raw_root
    assert Path(_flag_value(fade, "--output-audio-root")).resolve() == faded_root
    assert _flag_value(fade, "--fade-ms") == expected_fade_ms
    assert Path(_flag_value(render, "--audio-root")).resolve() == faded_root


def test_bgm_precedes_one_final_normalize_with_exact_input(tmp_path, monkeypatch):
    music = tmp_path / "music.wav"
    music.write_bytes(b"music")
    commands, long_video = _invoke(tmp_path, monkeypatch, [
        "--audio-source", "raw", "--build-long-video", "--normalize-audio",
        "--background-music", str(music),
    ])
    names = [_command_name(command) for command in commands]
    assert names == [
        "video-render", "video-join", "video-add-bgm", "video-normalize-audio",
    ]
    assert names.count("video-normalize-audio") == 1
    bgm = commands[names.index("video-add-bgm")]
    join = commands[names.index("video-join")]
    normalize = commands[names.index("video-normalize-audio")]
    assert _flag_value(join, "--narration-volume") == "1.0"
    assert _flag_value(bgm, "--narration-volume") == "1.2"
    assert Path(_flag_value(bgm, "--input")).resolve() == long_video.resolve()
    assert Path(_flag_value(normalize, "--input")).resolve() == long_video.resolve()
    assert "--replace" in bgm
    assert "--replace" in normalize


def test_normalize_runs_once_without_background_music(tmp_path, monkeypatch):
    commands, long_video = _invoke(tmp_path, monkeypatch, [
        "--audio-source", "raw", "--build-long-video", "--normalize-audio",
        "--no-background-music",
    ])
    names = [_command_name(command) for command in commands]
    assert names == ["video-render", "video-join", "video-normalize-audio"]
    assert _flag_value(commands[1], "--narration-volume") == "1.2"
    normalize = commands[-1]
    assert Path(_flag_value(normalize, "--input")).resolve() == long_video.resolve()
    assert "--replace" in normalize
