from __future__ import annotations

import argparse
from pathlib import Path

from mangaeasy.video_pipeline.common import (
    DEFAULT_AUDIO_ROOT,
    DEFAULT_PROJECT_ROOT,
    item_dirs,
    merge_item_selection,
    project_name,
)
from mangaeasy.video_pipeline.item_assets import load_narration


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check narration, panel, and audio counts/names for project items."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=DEFAULT_PROJECT_ROOT,
        help="Root folder containing item folders.",
    )
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    parser.add_argument("--project-name", default=None)
    parser.add_argument(
        "--items",
        nargs="*",
        help="Optional item folder names or ranges to check, for example: 01 02 05-08.",
    )
    parser.add_argument("--item-range", help="Convenience range, for example: 01-12.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 if any warning is found.",
    )
    return parser.parse_args()


def item_audio_dir(args: argparse.Namespace, item_dir: Path) -> Path:
    return args.audio_root.resolve() / project_name(args.project_root, args.project_name) / item_dir.name


def files_by_stem(folder: Path, extensions: set[str]) -> dict[str, Path]:
    if not folder.exists():
        return {}
    result: dict[str, Path] = {}
    for path in folder.iterdir():
        if path.is_file() and path.suffix.lower() in extensions:
            result[path.stem] = path
    return result


def warn(message: str, warnings: list[str]) -> None:
    warnings.append(message)
    print(f"  WARNING: {message}")


def check_item(item_dir: Path, args: argparse.Namespace) -> int:
    warnings: list[str] = []
    narration_path = item_dir / "narration.json"
    panels_dir = item_dir / "panels"
    audio_dir = item_audio_dir(args, item_dir)

    print(f"\n[{item_dir.name}]")

    narration = []
    if not narration_path.exists():
        warn(f"Missing {narration_path.name}", warnings)
    else:
        try:
            narration = load_narration(item_dir)
        except Exception as exc:
            warn(f"Could not read narration.json: {exc}", warnings)

    panels = files_by_stem(panels_dir, IMAGE_EXTENSIONS)
    audios = files_by_stem(audio_dir, AUDIO_EXTENSIONS)
    narration_images = [item.get("image", "") for item in narration if isinstance(item, dict)]
    narration_stems = [Path(name).stem for name in narration_images if name]

    print(f"  narration: {len(narration_stems)}")
    print(f"  panels:    {len(panels)}")
    print(f"  audio:     {len(audios)}")

    if not panels_dir.exists():
        warn("Missing panels folder", warnings)
    if not audio_dir.exists():
        warn(f"Missing audio folder: {audio_dir}", warnings)

    if len(narration_stems) != len(panels):
        warn(f"Narration count does not match panel count: {len(narration_stems)} vs {len(panels)}", warnings)
    if len(narration_stems) != len(audios):
        warn(f"Narration count does not match audio count: {len(narration_stems)} vs {len(audios)}", warnings)
    if len(panels) != len(audios):
        warn(f"Panel count does not match audio count: {len(panels)} vs {len(audios)}", warnings)

    duplicate_narration = sorted({stem for stem in narration_stems if narration_stems.count(stem) > 1})
    if duplicate_narration:
        warn("Duplicate narration image stems: " + ", ".join(duplicate_narration[:20]), warnings)

    narration_set = set(narration_stems)
    panel_set = set(panels)
    audio_set = set(audios)

    missing_panel = sorted(narration_set - panel_set)
    extra_panel = sorted(panel_set - narration_set)
    missing_audio = sorted(narration_set - audio_set)
    extra_audio = sorted(audio_set - narration_set)
    panel_without_audio = sorted(panel_set - audio_set)
    audio_without_panel = sorted(audio_set - panel_set)

    if missing_panel:
        warn("Narration references missing panels: " + ", ".join(missing_panel[:20]), warnings)
    if extra_panel:
        warn("Panels not listed in narration: " + ", ".join(extra_panel[:20]), warnings)
    if missing_audio:
        warn("Missing audio for narration entries: " + ", ".join(missing_audio[:20]), warnings)
    if extra_audio:
        warn("Audio not listed in narration: " + ", ".join(extra_audio[:20]), warnings)
    if panel_without_audio:
        warn("Panels without matching audio: " + ", ".join(panel_without_audio[:20]), warnings)
    if audio_without_panel:
        warn("Audio without matching panel: " + ", ".join(audio_without_panel[:20]), warnings)

    if not warnings:
        print("  OK: narration, panels, and audio match.")
    return len(warnings)


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    if not project_root.exists():
        raise FileNotFoundError(f"Project root does not exist: {project_root}")

    chapters = item_dirs(project_root, merge_item_selection(args.items, args.item_range))
    if not chapters:
        raise FileNotFoundError("No item folders found.")

    total_warnings = 0
    for item_dir in chapters:
        total_warnings += check_item(item_dir, args)

    print(f"\nChecked {len(chapters)} item folder(s). Warnings: {total_warnings}")
    if args.strict and total_warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
