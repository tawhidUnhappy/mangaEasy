from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from mangaeasy.video_pipeline.common import project_name
from mangaeasy.video_pipeline.ffmpeg_tools import probe_duration


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass(frozen=True)
class PanelAsset:
    image_path: Path
    audio_path: Path
    audio_duration: float
    visual_duration: float
    frame_count: int


def frame_aligned_duration(audio_duration: float, fps: int) -> tuple[float, int]:
    frames = max(1, math.ceil(audio_duration * fps))
    return frames / fps, frames


def load_narration(item_dir: Path) -> list[dict[str, str]]:
    """Load an item's narration entries, in playback order.

    If `intro.json` exists alongside `narration.json`, its entries are
    prepended -- a project-agnostic way to give one item (usually the first
    chapter) a cold-open trailer/hook reel without splicing it into the
    item's own narration.json. Same `{"image": ..., "narration": ...}` shape,
    same panels/ folder; every existing caller (audio generation, rendering,
    validation) sees one combined list and needs no changes.
    """
    path = item_dir / "narration.json"
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array.")

    intro_path = item_dir / "intro.json"
    if intro_path.exists():
        with intro_path.open("r", encoding="utf-8-sig") as f:
            intro_data = json.load(f)
        if not isinstance(intro_data, list):
            raise ValueError(f"{intro_path} must contain a JSON array.")
        data = intro_data + data
    return data


def item_audio_dir(audio_root: Path, project_root: Path, project_name_override: str | None, item_dir: Path) -> Path:
    return audio_root.resolve() / project_name(project_root, project_name_override) / item_dir.name


def item_narration_dir(audio_root: Path, project_root: Path, project_name_override: str | None) -> Path:
    return audio_root.resolve() / project_name(project_root, project_name_override) / "_items"


def item_narration_path(audio_root: Path, project_root: Path, project_name_override: str | None, item_dir: Path) -> Path:
    return item_narration_dir(audio_root, project_root, project_name_override) / f"item_{item_dir.name}_narration.wav"


def collect_panel_assets(
    item_dir: Path,
    *,
    project_root: Path,
    audio_root: Path,
    project_name_override: str | None,
    fps: int,
) -> list[PanelAsset]:
    assets: list[PanelAsset] = []
    audio_dir = item_audio_dir(audio_root, project_root, project_name_override, item_dir)
    for item in load_narration(item_dir):
        image_name = item.get("image")
        if not image_name:
            raise ValueError(f"Missing image key in {item_dir / 'narration.json'}")
        image_path = item_dir / "panels" / image_name
        audio_path = audio_dir / f"{Path(image_name).stem}.wav"
        if image_path.suffix.lower() not in IMAGE_EXTENSIONS or not image_path.exists():
            raise FileNotFoundError(f"Missing panel image: {image_path}")
        if not audio_path.exists():
            raise FileNotFoundError(f"Missing audio for {image_name}: {audio_path}. Run generate_audio.py first.")
        audio_duration = probe_duration(audio_path)
        visual_duration, frame_count = frame_aligned_duration(audio_duration, fps)
        assets.append(PanelAsset(image_path, audio_path, audio_duration, visual_duration, frame_count))
    return assets
