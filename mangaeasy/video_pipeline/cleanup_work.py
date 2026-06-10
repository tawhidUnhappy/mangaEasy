from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from mangaeasy.video_pipeline.common import DEFAULT_OUTPUT_ROOT, DEFAULT_PROJECT_ROOT, DEFAULT_WORK_DIR


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clean generated temp/output content while preserving all generated audio."
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--package-root", type=Path, default=Path.cwd())
    parser.add_argument("--yes", action="store_true", help="Actually delete. Default is dry run.")
    parser.add_argument("--include-cache", action=argparse.BooleanOptionalAction, default=False)
    return parser.parse_args()


def count_files(path: Path) -> int:
    if path.is_file():
        return 1
    return sum(1 for child in path.rglob("*") if child.is_file())


def add_if_exists(targets: list[Path], path: Path) -> None:
    if path.exists():
        targets.append(path.resolve())


def safe_targets(args: argparse.Namespace) -> list[Path]:
    package_root = args.package_root.resolve()
    output_root = args.output_root.resolve()
    work_dir = args.work_dir.resolve()
    targets: list[Path] = []
    add_if_exists(targets, output_root)
    add_if_exists(targets, work_dir)

    if args.include_cache:
        add_if_exists(targets, package_root / "__pycache__")
        for child in package_root.glob("*.pyc"):
            add_if_exists(targets, child)

    safe: list[Path] = []
    for target in sorted(set(targets), key=lambda p: str(p).lower()):
        if target == package_root:
            raise ValueError(f"Refusing to delete project root: {target}")
        if package_root not in target.parents:
            raise ValueError(f"Refusing unsafe delete target outside project root: {target}")
        if "audio" in [part.lower() for part in target.parts]:
            raise ValueError(f"Refusing to delete audio path: {target}")
        safe.append(target)
    return safe


def main() -> int:
    args = parse_args()
    targets = safe_targets(args)
    if not targets:
        print("No generated temp/output content found. Audio was preserved.")
        return 0

    action = "Deleting" if args.yes else "Dry run, would delete"
    print(f"{action} {len(targets)} target(s), preserving audio:")
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

    print("\nCleanup complete. Output and work folders were removed. Audio was preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
