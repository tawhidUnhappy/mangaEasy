from __future__ import annotations

import json

from mangaeasy import defaults


def _write_system_config(path, bgm):
    path.write_text(json.dumps({"bgm": bgm}), encoding="utf-8")


def test_default_background_music_uses_explicit_file(tmp_path, monkeypatch):
    cfg = tmp_path / "config.system.json"
    music = tmp_path / "one.wav"
    music.write_bytes(b"wav")
    _write_system_config(cfg, {"file": str(music), "volume_db": -26})
    monkeypatch.setattr(defaults, "SYSTEM_CONFIG_FILE", cfg)
    assert defaults.default_background_music() == music


def test_default_background_music_picks_first_file_from_directory(tmp_path, monkeypatch):
    cfg = tmp_path / "config.system.json"
    bgm_dir = tmp_path / "bgm"
    bgm_dir.mkdir()
    (bgm_dir / "b_track.wav").write_bytes(b"b")
    (bgm_dir / "a_track.mp3").write_bytes(b"a")
    _write_system_config(cfg, {"directory": str(bgm_dir), "volume_db": -26})
    monkeypatch.setattr(defaults, "SYSTEM_CONFIG_FILE", cfg)
    assert defaults.default_background_music() == bgm_dir / "a_track.mp3"

