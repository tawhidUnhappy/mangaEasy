from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from mangaeasy.video_pipeline.common import (
    DEFAULT_AUDIO_ROOT,
    DEFAULT_PROJECT_ROOT,
    item_dirs,
    merge_item_selection,
    project_name,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remove generated audio from make_video/audio.")
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--items", nargs="*", help="Item names or ranges, for example: 01 02 05-08.")
    parser.add_argument("--item-range", help="Convenience range, for example: 01-12.")
    parser.add_argument("--include-legacy", action="store_true", help="Also remove old item/audio folders if they exist.")
    parser.add_argument("--yes", action="store_true", help="Actually delete. Default is dry run.")
    return parser.parse_args()


def safe_generated_audio_targets(args: argparse.Namespace) -> list[Path]:
    audio_root = args.audio_root.resolve()
    manga_audio = (audio_root / project_name(args.project_root, args.project_name)).resolve()
    if audio_root not in manga_audio.parents:
        raise ValueError(f"Refusing unsafe audio path: {manga_audio}")
    selected = merge_item_selection(args.items, args.item_range)
    if selected:
        targets = [(manga_audio / ch).resolve() for ch in selected]
    else:
        targets = [manga_audio]
    safe = []
    for target in targets:
        if not target.exists():
            continue
        if not target.is_dir():
            raise ValueError(f"Expected directory: {target}")
        if audio_root not in target.parents:
            raise ValueError(f"Refusing to delete unsafe path: {target}")
        safe.append(target)
    return safe


def safe_audio_dir(root: Path, chapter_dir: Path) -> Path | None:
    audio_dir = (chapter_dir / "audio").resolve()
    root = root.resolve()
    if not audio_dir.exists():
        return None
    if not audio_dir.is_dir():
        raise ValueError(f"Expected directory: {audio_dir}")
    if audio_dir.name.lower() != "audio" or root not in audio_dir.parents:
        raise ValueError(f"Refusing to delete unsafe path: {audio_dir}")
    return audio_dir


def count_files(path: Path) -> int:
    return sum(1 for p in path.rglob("*") if p.is_file())


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    targets = [(path, count_files(path)) for path in safe_generated_audio_targets(args)]
    if args.include_legacy:
        for chapter_dir in item_dirs(project_root, merge_item_selection(args.items, args.item_range)):
            audio_dir = safe_audio_dir(project_root, chapter_dir)
            if audio_dir:
                targets.append((audio_dir, count_files(audio_dir)))

    if not targets:
        print("No generated audio folders found.")
        return 0

    action = "Deleting" if args.yes else "Dry run, would delete"
    print(f"{action} {len(targets)} audio folder(s):")
    for audio_dir, file_count in targets:
        print(f"  {audio_dir} ({file_count} file(s))")

    if not args.yes:
        print("\nRun again with --yes to delete them.")
        return 0

    for audio_dir, _ in targets:
        shutil.rmtree(audio_dir)
    print("\nCleanup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
