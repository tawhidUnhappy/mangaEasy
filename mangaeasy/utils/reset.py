#!/usr/bin/env python3
"""mangaeasy.utils.reset — reset chapter number to 1 in config.json."""

import argparse
import json
import sys
from pathlib import Path

from mangaeasy.config import CONFIG_FILE
from mangaeasy.utils import atomic_write_json


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", "-c", default=str(CONFIG_FILE))
    ap.add_argument("--chapter", type=int, default=1, help="Chapter to reset to (default: 1)")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"[error] Config not found: {cfg_path}")
        sys.exit(1)

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["download"]["chapter"] = args.chapter

    if not atomic_write_json(cfg_path, cfg):
        sys.exit(3)

    print(f"[ok] Chapter reset to {args.chapter} in {cfg_path}")


if __name__ == "__main__":
    main()
