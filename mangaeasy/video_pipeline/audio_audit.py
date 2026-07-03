from __future__ import annotations

import argparse
import subprocess
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
MIN_AUDIO_SECONDS = 0.05


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify every panel has a matching, readable, non-empty audio file before rendering."
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--items", nargs="*", help="Item names or ranges, for example: 01 02 05-08.")
    parser.add_argument("--item-range", help="Convenience range, for example: 01-12.")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Delete corrupt/empty audio files so the next audio-generation run regenerates "
             "exactly those (generation skips files that already exist).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit one JSON object on stdout instead of the human report.",
    )
    return parser.parse_args()


def item_audio_dir(audio_root: Path, name: str, item_dir: Path) -> Path:
    return audio_root / name / item_dir.name


def ffprobe_duration(path: Path) -> float | None:
    """Return audio duration in seconds, or None if the file is missing/unreadable/corrupt."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        value = result.stdout.strip()
        return float(value) if value else None
    except (subprocess.CalledProcessError, ValueError, OSError):
        return None


def audit_item(
    item_dir: Path,
    audio_root: Path,
    name: str,
    missing_panels: list[tuple[str, str]],
    bad_audio: list[tuple[str, str, Path]],
    not_ready: list[str],
) -> int:
    if not (item_dir / "narration.json").exists():
        not_ready.append(item_dir.name)
        return 0

    audio_dir = item_audio_dir(audio_root, name, item_dir)
    panels_dir = item_dir / "panels"
    narration = load_narration(item_dir)
    checked = 0

    for entry in narration:
        image_name = entry.get("image") if isinstance(entry, dict) else None
        if not image_name:
            continue
        panel_path = panels_dir / image_name
        if not panel_path.exists():
            missing_panels.append((item_dir.name, image_name))

        checked += 1
        audio_path = audio_dir / f"{Path(image_name).stem}.wav"
        if not audio_path.exists():
            bad_audio.append((item_dir.name, image_name, audio_path))
            continue
        duration = ffprobe_duration(audio_path)
        if duration is None or duration < MIN_AUDIO_SECONDS:
            bad_audio.append((item_dir.name, image_name, audio_path))

    return checked


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    audio_root = args.audio_root.resolve()
    name = project_name(args.project_root, args.project_name)

    if not project_root.exists():
        raise FileNotFoundError(f"Project root does not exist: {project_root}")

    selected = item_dirs(project_root, merge_item_selection(args.items, args.item_range))
    if not selected:
        raise FileNotFoundError(f"No item folders selected under {project_root}")

    missing_panels: list[tuple[str, str]] = []
    bad_audio: list[tuple[str, str, Path]] = []
    not_ready: list[str] = []
    total_checked = 0
    for item_dir in selected:
        total_checked += audit_item(item_dir, audio_root, name, missing_panels, bad_audio, not_ready)

    checked_items = len(selected) - len(not_ready)
    ok = not missing_panels and not bad_audio

    removed = 0
    if bad_audio and args.fix:
        for _item_name, _image_name, audio_path in bad_audio:
            if audio_path.exists():
                audio_path.unlink()
                removed += 1

    if args.as_json:
        import json

        print(json.dumps({
            "checked_entries": total_checked,
            "checked_items": checked_items,
            "not_ready": not_ready,
            "missing_panels": [{"item": i, "image": img} for i, img in missing_panels],
            "bad_audio": [{"item": i, "image": img, "path": str(p)} for i, img, p in bad_audio],
            "fixed_deleted": removed,
            "ok": ok,
        }, ensure_ascii=False))
        return 0 if ok else 1

    print(f"Checked {total_checked} narration entry/entries across {checked_items} item folder(s).")
    if not_ready:
        print(f"Skipped {len(not_ready)} item folder(s) with no narration.json yet (not ready for audio): "
              + ", ".join(not_ready[:30]) + ("..." if len(not_ready) > 30 else ""))

    if missing_panels:
        print(f"\n{len(missing_panels)} narration entr(y/ies) reference a panel image that does not exist "
              "(audio cannot fix this -- the source artwork is missing):")
        for item_name, image_name in missing_panels[:30]:
            print(f"  [{item_name}] {image_name}")
        if len(missing_panels) > 30:
            print(f"  ...and {len(missing_panels) - 30} more")

    if bad_audio:
        print(f"\n{len(bad_audio)} missing/corrupt/empty audio file(s):")
        for item_name, image_name, audio_path in bad_audio[:30]:
            print(f"  [{item_name}] {image_name} -> {audio_path}")
        if len(bad_audio) > 30:
            print(f"  ...and {len(bad_audio) - 30} more")

    if ok:
        print("\nAUDIO AUDIT OK: every panel has valid, readable audio.")
        return 0

    if not bad_audio:
        return 1

    if not args.fix:
        print("\nRun again with --fix to delete the bad audio files, then re-run audio generation "
              "to regenerate exactly those (it skips files that already exist).")
        return 1

    print(f"\nDeleted {removed} corrupt/empty audio file(s) (missing ones were already absent). "
          "Run audio generation again to regenerate them, then re-run this audit to confirm.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
