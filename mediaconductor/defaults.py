"""Default media paths used by the video and TTS workflows."""

from __future__ import annotations

import json
from pathlib import Path

from mediaconductor.config import PROJECT_ROOT, SYSTEM_CONFIG_FILE

DEFAULT_BACKGROUND_MUSIC = Path("media/background-music.wav")
DEFAULT_BACKGROUND_MUSIC_DIR = Path("bgm")
DEFAULT_SPEAKER_WAV = Path("media/speaker-reference.wav")
# -28 keeps the bed comfortably in the background for long-form recap
# watching (previously -26, which read as loud/fatiguing over a full video).
# -26 to -22 suits punchier or sparser edits that want the bed to read more.
DEFAULT_MUSIC_VOLUME_DB = -28.0
DEFAULT_NARRATION_VOLUME = 1.2
DEFAULT_TTS_ENGINE = "auto"
DEFAULT_MANGA_VIDEO_AUDIO_SOURCE = "faded"
DEFAULT_MANGA_VIDEO_AUDIO_FADE_MS = 8.0
_MUSIC_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


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


def _pick_music_file(path: Path) -> Path | None:
    if path.is_file():
        return path
    if path.is_dir():
        for candidate in sorted(path.iterdir()):
            if candidate.is_file() and candidate.suffix.lower() in _MUSIC_EXTS:
                return candidate
    return None


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


def default_manga_video_audio_source() -> str:
    """Return the safe manga-video audio derivative from system config."""
    cfg = _system_config().get("manga_video", {})
    value = str(cfg.get("audio_source", DEFAULT_MANGA_VIDEO_AUDIO_SOURCE)).strip().lower()
    return value if value in {"raw", "faded"} else DEFAULT_MANGA_VIDEO_AUDIO_SOURCE


def default_manga_video_audio_fade_ms() -> float:
    cfg = _system_config().get("manga_video", {})
    try:
        value = float(cfg.get("audio_fade_ms", DEFAULT_MANGA_VIDEO_AUDIO_FADE_MS))
    except (TypeError, ValueError):
        return DEFAULT_MANGA_VIDEO_AUDIO_FADE_MS
    return value if value > 0 else DEFAULT_MANGA_VIDEO_AUDIO_FADE_MS


def configured_background_music() -> Path:
    cfg = _system_config().get("bgm", {})
    explicit = cfg.get("file") or cfg.get("path")
    directory = cfg.get("directory") or cfg.get("dir")

    if explicit:
        chosen = _pick_music_file(project_path(explicit))
        if chosen is not None:
            return chosen

    if directory:
        chosen = _pick_music_file(project_path(directory))
        if chosen is not None:
            return chosen

    chosen = _pick_music_file(DEFAULT_BACKGROUND_MUSIC)
    if chosen is not None:
        return chosen

    chosen = _pick_music_file(DEFAULT_BACKGROUND_MUSIC_DIR)
    if chosen is not None:
        return chosen

    return project_path(cfg.get("file") or DEFAULT_BACKGROUND_MUSIC)


def default_background_music() -> Path | None:
    path = configured_background_music()
    return path if path.is_file() else None
