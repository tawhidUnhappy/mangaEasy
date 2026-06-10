#!/usr/bin/env python3
"""mangaeasy.narration.backup — back up the narration.json file."""

import sys
from pathlib import Path

from mangaeasy.config import load_download_config
from mangaeasy.paths import narration_json, chapter_dir


def get_safe_backup_name(folder: Path) -> Path:
    base = folder / "backup_narration.json"
    if not base.exists():
        return base
    n = 1
    while True:
        candidate = folder / f"backup_narration_{n}.json"
        if not candidate.exists():
            return candidate
        n += 1


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    narr    = narration_json()
    ch_dir  = narr.parent

    print(f"[INFO] Chapter dir: {ch_dir}")
    print(f"[INFO] Dry run    : {dry_run}")

    if not ch_dir.exists():
        print("[ERROR] Chapter directory not found.")
        sys.exit(1)

    if not narr.exists():
        print("[INFO] narration.json not found — nothing to back up.")
        return

    backup = get_safe_backup_name(ch_dir)

    if dry_run:
        print(f"[DRY-RUN] Would rename: {narr} -> {backup}")
        return

    narr.rename(backup)
    print(f"[RENAMED] {narr.name} -> {backup.name}")
    print("[DONE]")


if __name__ == "__main__":
    main()
