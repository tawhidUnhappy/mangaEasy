"""Default media paths used by the video and TTS workflows."""

from __future__ import annotations

import json
from pathlib import Path

from mangaeasy.config import PROJECT_ROOT, SYSTEM_CONFIG_FILE

DEFAULT_BACKGROUND_MUSIC = Path("music/Thapin_by_the_sea.wav")
DEFAULT_SPEAKER_WAV = Path("vocal/manga_vocal2.wav")
DEFAULT_MUSIC_VOLUME_DB = -26.0
DEFAULT_NARRATION_VOLUME = 1.2
DEFAULT_TTS_ENGINE = "auto"


def _system_config() -> dict:
    if not SYSTEM_CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(SYSTEM_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def project_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def default_speaker_wav() -> Path:
    cfg = _system_config().get("tts", {})
    return project_path(cfg.get("speaker_wav") or DEFAULT_SPEAKER_WAV)


def default_tts_engine() -> str:
    cfg = _system_config().get("tts", {})
    value = str(cfg.get("engine", DEFAULT_TTS_ENGINE)).strip().lower()
    return value if value in {"auto", "indextts", "kokoro"} else DEFAULT_TTS_ENGINE


def default_music_volume_db() -> float:
    cfg = _system_config().get("bgm", {})
    try:
        return float(cfg.get("volume_db", DEFAULT_MUSIC_VOLUME_DB))
    except (TypeError, ValueError):
        return DEFAULT_MUSIC_VOLUME_DB


def configured_background_music() -> Path:
    cfg = _system_config().get("bgm", {})
    return project_path(cfg.get("file") or DEFAULT_BACKGROUND_MUSIC)


def default_background_music() -> Path | None:
    path = configured_background_music()
    return path if path.is_file() else None
