from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from mangaeasy.video_pipeline.common import (
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PROJECT_ROOT,
    merge_item_selection,
    project_name,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Delete generated item videos and long videos.")
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--items", nargs="*", help="Only delete selected item videos, for example: 01 02 05-08.")
    parser.add_argument("--item-range", help="Convenience range, for example: 01-12.")
    parser.add_argument("--include-long", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--yes", action="store_true", help="Actually delete. Default is dry run.")
    parser.add_argument(
        "--include-legacy",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Also remove old output files directly under output-root.",
    )
    return parser.parse_args()


def count_files(path: Path) -> int:
    if path.is_file():
        return 1
    return sum(1 for child in path.rglob("*") if child.is_file())


def safe_targets(
    output_root: Path,
    project: str,
    include_legacy: bool,
    chapters: list[str] | None,
    include_long: bool,
) -> list[Path]:
    root = output_root.resolve()
    targets: list[Path] = []

    project_output = (root / project).resolve()
    if chapters:
        item_dir = project_output / "items"
        legacy_chapter_dir = project_output / "chapters"
        for chapter in chapters:
            target = (item_dir / f"item_{chapter}.mp4").resolve()
            if target.exists():
                targets.append(target)
            legacy_target = (legacy_chapter_dir / f"chapter_{chapter}.mp4").resolve()
            if legacy_target.exists():
                targets.append(legacy_target)
        if include_long:
            long_video = (project_output / f"{project}_full.mp4").resolve()
            if long_video.exists():
                targets.append(long_video)
    elif project_output.exists():
        targets.append(project_output)

    if include_legacy and root.exists():
        for path in root.glob("chapter_*.mp4"):
            targets.append(path.resolve())
        for path in root.glob("item_*.mp4"):
            targets.append(path.resolve())
        for path in root.glob("*full*.mp4"):
            targets.append(path.resolve())

    safe: list[Path] = []
    for target in targets:
        if target == root or root not in target.parents:
            raise ValueError(f"Refusing unsafe delete target: {target}")
        safe.append(target)
    return sorted(set(safe), key=lambda p: str(p).lower())


def main() -> int:
    args = parse_args()
    name = project_name(args.project_root, args.project_name)
    chapters = merge_item_selection(args.items, args.item_range)
    targets = safe_targets(args.output_root, name, args.include_legacy and not chapters, chapters, args.include_long)

    if not targets:
        print("No generated videos found.")
        return 0

    action = "Deleting" if args.yes else "Dry run, would delete"
    print(f"{action} {len(targets)} target(s):")
    for target in targets:
        kind = "folder" if target.is_dir() else "file"
        print(f"  {target} ({kind}, {count_files(target)} file(s))")

    if not args.yes:
        print("\nRun again with --yes to delete them.")
        return 0

    for target in targets:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)
    print("\nVideo cleanup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
