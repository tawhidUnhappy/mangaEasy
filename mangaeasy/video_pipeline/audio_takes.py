from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from mangaeasy.utils import LazyArchiveRunDir, archive_into_run
from mangaeasy.video_pipeline.common import (
    DEFAULT_AUDIO_ROOT,
    DEFAULT_PROJECT_ROOT,
    project_name,
)

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac"}
RUN_NAME_RE = re.compile(r"run_(\d+)")


def manga_audio_dir(args: argparse.Namespace) -> Path:
    return (args.audio_root.resolve() / project_name(args.project_root, args.project_name)).resolve()


def _item_file_counts(folder: Path) -> dict[str, int]:
    """Count audio files per immediate subfolder (an item, e.g. "01")."""
    counts: dict[str, int] = {}
    if not folder.exists():
        return counts
    for entry in folder.iterdir():
        if not entry.is_dir() or entry.name == "old":
            continue
        n = sum(1 for f in entry.rglob("*") if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS)
        if n:
            counts[entry.name] = n
    return counts


def list_runs(manga_audio: Path) -> list[dict]:
    old_root = manga_audio / "old"
    if not old_root.exists():
        return []
    runs = []
    for entry in sorted(old_root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or not RUN_NAME_RE.fullmatch(entry.name):
            continue
        items = _item_file_counts(entry)
        runs.append(
            {
                "run": entry.name,
                "archived_at": datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc).isoformat(),
                "items": items,
                "total_files": sum(items.values()),
            }
        )
    return runs


def list_main_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List previously archived audio takes (created whenever audio was "
                     "regenerated or cleared) so you can restore one instead of generating fresh."
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of a table.")
    return parser.parse_args()


def list_main() -> int:
    args = list_main_args()
    manga_audio = manga_audio_dir(args)
    active = {"items": _item_file_counts(manga_audio)}
    active["total_files"] = sum(active["items"].values())
    runs = list_runs(manga_audio)

    if args.json:
        print(json.dumps({"active": active, "runs": runs}, indent=2))
        return 0

    print(f"Active audio: {manga_audio}")
    if active["items"]:
        for item, count in sorted(active["items"].items()):
            print(f"  {item}: {count} file(s)")
    else:
        print("  (none)")

    if not runs:
        print("\nNo archived takes yet -- they're created automatically whenever audio "
              "is regenerated, resumed, or cleared (`video-clean-audio`).")
        return 0

    print(f"\n{len(runs)} archived take(s) under {manga_audio / 'old'}:")
    for run in runs:
        print(f"  {run['run']}  ({run['archived_at']})  {run['total_files']} file(s)")
        for item, count in sorted(run["items"].items()):
            print(f"    {item}: {count} file(s)")
    return 0


def restore_main_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restore a previously archived audio take as the active audio, instead of "
                     "regenerating it. Whatever is currently active gets archived first, so "
                     "nothing is ever lost -- list takes first with `audio-takes-list`."
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--run", required=True, help="Run folder to restore, for example: run_0002.")
    parser.add_argument("--items", nargs="*",
                         help="Restore only these item folders from the run (default: every item present in it).")
    return parser.parse_args()


def restore_main() -> int:
    args = restore_main_args()
    manga_audio = manga_audio_dir(args)
    run_dir = manga_audio / "old" / args.run
    if not run_dir.is_dir():
        print(f"[FATAL] No such archived take: {run_dir}")
        return 1

    available_items = sorted(p.name for p in run_dir.iterdir() if p.is_dir())
    wanted_items = args.items if args.items else available_items
    missing = [item for item in wanted_items if item not in available_items]
    if missing:
        print(f"[FATAL] {args.run} has no audio for: {', '.join(missing)} (has: {', '.join(available_items)})")
        return 1

    archive_run_dir = LazyArchiveRunDir(manga_audio / "old")
    restored_files = 0
    for item in wanted_items:
        source_dir = run_dir / item
        active_dir = manga_audio / item
        active_dir.mkdir(parents=True, exist_ok=True)
        for source_file in source_dir.iterdir():
            if not source_file.is_file():
                continue
            active_file = active_dir / source_file.name
            if active_file.exists():
                archive_into_run(active_file, archive_run_dir.dir, subdir=item)
            shutil.copy2(source_file, active_file)
            restored_files += 1

    if archive_run_dir.allocated is not None:
        print(f"Archived the audio that was active before this restore to: {archive_run_dir.allocated}")
    print(f"Restored {restored_files} file(s) from {args.run} into {manga_audio} for: {', '.join(wanted_items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(list_main())
