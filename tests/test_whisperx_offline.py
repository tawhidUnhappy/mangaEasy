from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from mangaeasy.tools import install
from mangaeasy.tools import whisperx as whisperx_cli


def _load_adapter():
    path = Path(install.__file__).resolve().parents[1] / "assets" / "tools" / "transcribe_whisperx.py"
    spec = importlib.util.spec_from_file_location("transcribe_whisperx_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_whisperx_spec_installs_wheel_bundled_silero_vad():
    assert "silero-vad==6.2.1" in install.TOOLS["whisperx"].env_deps


def test_offline_silero_hub_serves_only_local_wheel(monkeypatch):
    adapter = _load_adapter()
    local_model = object()
    utilities = [lambda: None for _index in range(5)]
    silero_vad = ModuleType("silero_vad")
    silero_vad.load_silero_vad = lambda *, onnx: local_model if onnx is False else None
    (
        silero_vad.get_speech_timestamps,
        silero_vad.save_audio,
        silero_vad.read_audio,
        silero_vad.VADIterator,
        silero_vad.collect_chunks,
    ) = utilities
    monkeypatch.setitem(sys.modules, "silero_vad", silero_vad)

    unexpected_calls: list[tuple] = []
    torch_module = SimpleNamespace(
        hub=SimpleNamespace(load=lambda *args, **kwargs: unexpected_calls.append((args, kwargs))),
    )
    adapter._install_offline_silero_hub(torch_module)

    model, loaded_utilities = torch_module.hub.load(
        repo_or_dir="snakers4/silero-vad",
        model="silero_vad",
        force_reload=False,
        onnx=False,
        trust_repo=True,
    )
    assert model is local_model
    assert loaded_utilities == tuple(utilities)
    assert unexpected_calls == []

    with pytest.raises(RuntimeError, match="unexpected repository"):
        torch_module.hub.load("someone/network-model", "silero_vad")
    with pytest.raises(RuntimeError, match="unexpected model"):
        torch_module.hub.load("snakers4/silero-vad", "different_model")
    with pytest.raises(RuntimeError, match="only provisions Silero JIT"):
        torch_module.hub.load("snakers4/silero-vad", "silero_vad", onnx=True)


def test_bundled_adapter_accepts_only_bounded_minimum_confidence(tmp_path):
    adapter = _load_adapter()
    required = [
        "--audio", str(tmp_path / "vocals.wav"),
        "--output", str(tmp_path / "raw.json"),
        "--model", str(tmp_path / "model"),
        "--align-model", str(tmp_path / "align-model"),
    ]

    assert adapter.parse_args([*required, "--minimum-confidence", "0.35"]).minimum_confidence == 0.35
    assert adapter.parse_args(required).minimum_confidence == 0.72
    with pytest.raises(SystemExit):
        adapter.parse_args([*required, "--minimum-confidence", "1.01"])


def test_whisperx_cli_forwards_and_applies_minimum_confidence(
    tmp_path, monkeypatch,
):
    tool_dir = tmp_path / "whisperx-tool"
    tool_dir.mkdir()
    audio = tmp_path / "vocals.wav"
    lyrics = tmp_path / "lyrics.txt"
    output_dir = tmp_path / "alignment"
    audio.write_bytes(b"fixture")
    lyrics.write_text("We rise", encoding="utf-8")
    captured = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        raw_path = Path(command[command.index("--output") + 1])
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(json.dumps({
            "word_segments": [
                {"word": "we", "start": 0.0, "end": 0.2},
                {"word": "rize", "start": 0.3, "end": 0.5},
            ],
        }), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(whisperx_cli, "resolve_tool_dir", lambda *_args, **_kwargs: tool_dir)
    monkeypatch.setattr(whisperx_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mediaconductor whisperx",
            "--audio", str(audio),
            "--lyrics-file", str(lyrics),
            "--output-dir", str(output_dir),
            "--minimum-confidence", "0.5",
        ],
    )

    assert whisperx_cli.main() == 0
    command = captured["command"]
    assert command[command.index("--minimum-confidence") + 1] == "0.5"
    aligned = json.loads((output_dir / "timed_lyrics.json").read_text(encoding="utf-8"))
    assert aligned["confidence"] == 0.5
    assert aligned["minimum_confidence"] == 0.5
    assert aligned["review_required"] is False
