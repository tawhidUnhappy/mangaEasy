from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from mangaeasy.video_pipeline.common import DEFAULT_PROJECT_ROOT


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete ALL generated output for a project in one go -- narration audio "
                     "(including archived takes), rendered item videos, and the joined long "
                     "video. Source chapters (panels/narration/downloads under --project-root) "
                     "are never touched; only --dir itself is removed."
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT,
                         help="The manga's library folder, used only as a safety check against --dir.")
    parser.add_argument("--dir", type=Path, required=True,
                         help="The manga-specific output directory to delete wholesale "
                              "(the desktop app's 'projectOutputDir', e.g. library/<manga>/output).")
    parser.add_argument("--yes", action="store_true", help="Actually delete. Default is dry run.")
    return parser.parse_args()


def count_files(path: Path) -> int:
    return sum(1 for p in path.rglob("*") if p.is_file())


def total_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    target = args.dir.resolve()

    if not target.exists():
        print(f"Nothing to delete: {target} does not exist.")
        return 0
    if target == project_root or target in project_root.parents:
        raise SystemExit(
            f"[FATAL] Refusing to delete {target} -- it is project-root ({project_root}) "
            "or an ancestor of it. --dir must be the project's own generated-output folder, "
            "not its source library folder."
        )

    files = count_files(target)
    size_gb = total_size(target) / 1024**3
    action = "Deleting" if args.yes else "Dry run, would delete"
    print(f"{action}: {target} ({files} file(s), {size_gb:.2f} GB)")

    if not args.yes:
        print("\nRun again with --yes to delete it.")
        return 0

    shutil.rmtree(target)
    print("\nDeleted. Source chapters (panels/narration/downloads) were not touched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
