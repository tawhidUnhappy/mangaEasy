"""mangaeasy.images.ai_zip_cli — CLI wrapper for panels_to_ai_zip.

Exposed as `mangaeasy ai-zip`. Lets non-Python frontends (the Electron
desktop app) trigger an AI-context ZIP export without importing the package.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from mangaeasy.images.ai_zip import panels_to_ai_zip


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pack chapter panels into a labelled ZIP for AI context.")
    parser.add_argument("--panels-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    panels_dir = args.panels_dir.resolve()
    output = args.output.resolve()
    if not panels_dir.is_dir():
        print(f"[FATAL] Panels folder not found: {panels_dir}")
        return 1

    def progress(value: int, total: int, label: str) -> None:
        print(f"MANGAEASY_PROGRESS {value}/{total} {label}", flush=True)

    count = panels_to_ai_zip(panels_dir, output, log=print, progress=progress)
    print(f"[ai-zip] {count} panels -> {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
