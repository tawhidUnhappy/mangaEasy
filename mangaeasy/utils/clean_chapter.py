#!/usr/bin/env python3
"""mangaeasy.utils.clean_chapter — remove temp files from a chapter directory."""

import argparse
import shutil
import sys
from pathlib import Path

from mangaeasy.config import load_download_config
from mangaeasy.paths import chapter_dir


def safe_remove(path: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"[DRY-RUN] Would delete: {path}")
        return
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
        print(f"[DELETED] {path}")
    except Exception as exc:
        print(f"[WARN] Failed to delete {path}: {exc}")


def main() -> None:
    # Set up argparse for strict command-line parameters
    parser = argparse.ArgumentParser(
        description="Clean up the chapter directory by removing temporary files."
    )
    parser.add_argument(
        "-kd",
        "--keep-dirs",
        nargs="*",
        required=True,  # STRICT: User MUST provide this flag
        help="REQUIRED: List of directory names to keep (e.g., -kd download panels). Leave empty after flag to keep none.",
    )
    parser.add_argument(
        "-kf",
        "--keep-files",
        nargs="*",
        required=True,  # STRICT: User MUST provide this flag
        help="REQUIRED: List of file names to keep (e.g., -kf narration.json my_file.txt). Leave empty after flag to keep none.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting.",
    )

    # If the user doesn't provide -kd and -kf, the script stops right here.
    args = parser.parse_args()

    # Load config and determine paths
    dl = load_download_config()
    name = str(dl["name"])
    chapter = int(dl["chapter"])

    ch_dir = chapter_dir(name, chapter)

    if not ch_dir.exists() or not ch_dir.is_dir():
        print(f"[ERROR] Chapter directory not found: {ch_dir}")
        sys.exit(1)

    # Strict assignment: Exactly what the user passed, no fallbacks!
    keep_dirs = set(args.keep_dirs)
    keep_files = set(args.keep_files)

    print(f"[INFO] Directory  : {ch_dir}")
    print(f"[INFO] Keep Dirs  : {keep_dirs}")
    print(f"[INFO] Keep Files : {keep_files}")
    print(f"[INFO] Dry run    : {args.dry_run}")
    print("-" * 40)

    # Perform Cleanup
    for item in ch_dir.iterdir():
        if item.is_dir() and item.name in keep_dirs:
            continue
        if item.is_file() and item.name in keep_files:
            continue
        safe_remove(item, dry_run=args.dry_run)

    print("[DONE] Cleanup finished.")


if __name__ == "__main__":
    main()
