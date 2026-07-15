from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from mangaeasy.brand import CLI_NAME
from mangaeasy.utils import LazyArchiveRunDir
from mangaeasy.video_pipeline.common import (
    DEFAULT_AUDIO_ROOT,
    DEFAULT_PROJECT_ROOT,
    item_dirs,
    merge_item_selection,
    project_name,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clear generated audio from make_video/audio so it regenerates fresh, "
                     "without losing the previous take -- it's archived into old/run_NNNN/ "
                     f"first (see `{CLI_NAME} audio-takes-list`/`audio-takes-restore`)."
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--items", nargs="*", help="Item names or ranges, for example: 01 02 05-08.")
    parser.add_argument("--item-range", help="Convenience range, for example: 01-12.")
    parser.add_argument("--include-legacy", action="store_true", help="Also clear old item/audio folders if they exist.")
    parser.add_argument("--purge", action="store_true",
                         help="Permanently delete instead of archiving. Use this only if you really "
                              "don't want the previous take back -- audio is expensive to regenerate.")
    parser.add_argument("--yes", action="store_true", help="Actually clear. Default is dry run.")
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
    manga_audio = (args.audio_root.resolve() / project_name(args.project_root, args.project_name)).resolve()

    targets: list[tuple[Path, int, str]] = [
        (path, count_files(path), path.name) for path in safe_generated_audio_targets(args)
    ]
    if args.include_legacy:
        for chapter_dir in item_dirs(project_root, merge_item_selection(args.items, args.item_range)):
            audio_dir = safe_audio_dir(project_root, chapter_dir)
            if audio_dir:
                targets.append((audio_dir, count_files(audio_dir), f"legacy_{chapter_dir.name}"))

    # The whole-project target (no --items given) includes manga_audio's own
    # old/ archive folder as a child -- never sweep that into itself.
    targets = [(path, count, label) for path, count, label in targets if path.name != "old"]

    if not targets:
        print("No generated audio folders found.")
        return 0

    verb = "permanently delete" if args.purge else "archive (restorable later)"
    prefix = "Dry run, would" if not args.yes else "Will"
    print(f"{prefix} {verb} {len(targets)} audio folder(s):")
    for audio_dir, file_count, _ in targets:
        print(f"  {audio_dir} ({file_count} file(s))")

    if not args.yes:
        print("\nRun again with --yes to apply.")
        return 0

    if args.purge:
        for audio_dir, _, _ in targets:
            shutil.rmtree(audio_dir)
        print("\nPermanently deleted.")
        return 0

    archive_run_dir = LazyArchiveRunDir(manga_audio / "old")
    for audio_dir, _, label in targets:
        destination = archive_run_dir.dir / label
        destination.mkdir(parents=True, exist_ok=True)
        # audio_dir may be manga_audio itself (whole-project target) which
        # has the "old" archive folder as a child -- move its other children
        # individually rather than the directory itself, so "old" is never
        # swept into its own descendant.
        for child in audio_dir.iterdir():
            if audio_dir == manga_audio and child.name == "old":
                continue
            child_destination = destination / child.name
            if child_destination.exists():
                shutil.rmtree(child_destination) if child_destination.is_dir() else child_destination.unlink()
            shutil.move(str(child), str(child_destination))
        if audio_dir != manga_audio:
            audio_dir.rmdir()
    print(f"\nArchived to: {archive_run_dir.dir}")
    print(f"Pick it back up with `{CLI_NAME} audio-takes-restore`, or list takes with `{CLI_NAME} audio-takes-list`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
