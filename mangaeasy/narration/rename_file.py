#!/usr/bin/env python3
"""mangaeasy.narration.rename_file — safely rename any file in the chapter directory."""

import argparse
import sys
from pathlib import Path

# Importing chapter_dir from your existing paths module
from mangaeasy.paths import chapter_dir


def main() -> None:
    # Set up explicit, structured command-line arguments
    parser = argparse.ArgumentParser(
        description="Rename a specific file inside the current chapter directory."
    )
    parser.add_argument(
        "-i",
        "--input",
        required=True,
        help="The exact current name of the file (e.g., 'data.txt')",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="The new name for the file (e.g., 'data_old.txt')",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the output file if it already exists",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without actually renaming the file",
    )

    args = parser.parse_args()

    # Get the chapter directory using your existing paths config
    ch_dir = chapter_dir()

    print(f"[INFO] Chapter dir: {ch_dir}")

    if not ch_dir.exists():
        print("[ERROR] Chapter directory not found.")
        sys.exit(1)

    # Build the full paths
    source_file = ch_dir / args.input
    dest_file = ch_dir / args.output

    # Safety Check 1: Does the input file exist?
    if not source_file.exists():
        print(f"[ERROR] Source file '{args.input}' not found in the chapter directory.")
        sys.exit(1)

    # Safety Check 2: Does the output file already exist?
    if dest_file.exists() and not args.force:
        print(f"[ERROR] Destination file '{args.output}' already exists!")
        print("        Use the --force flag if you want to overwrite it.")
        sys.exit(1)

    # Handle Dry Run
    if args.dry_run:
        print(f"[DRY-RUN] Would rename: {source_file.name} -> {dest_file.name}")
        return

    # Perform the rename
    # Note: If replacing an existing file in Python 3.8+, replace() is safer than rename()
    # when overwriting on some operating systems.
    source_file.replace(dest_file)

    print(f"[SUCCESS] Renamed '{source_file.name}' to '{dest_file.name}'")


if __name__ == "__main__":
    main()
