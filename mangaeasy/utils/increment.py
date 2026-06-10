#!/usr/bin/env python3
"""mangaeasy.utils.increment — atomically increment chapter number in config.json."""

import argparse
import json
import sys
from pathlib import Path

from mangaeasy.config import CONFIG_FILE
from mangaeasy.utils import atomic_write_json


def read_config(path: Path) -> dict | None:
    if not path.exists():
        print(f"[error] Config file not found: {path}")
        return None
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[error] Failed to parse {path}: {exc}")
        return None
    if "chapter" not in cfg.get("download", {}):
        print("[error] 'chapter' missing from config.json download section")
        return None
    cfg["download"]["chapter"] = int(cfg["download"]["chapter"])
    return cfg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", "-c", default=str(CONFIG_FILE))
    args = ap.parse_args()

    cfg_path = Path(args.config)
    cfg = read_config(cfg_path)
    if cfg is None:
        sys.exit(2)

    old = cfg["download"]["chapter"]
    cfg["download"]["chapter"] = old + 1

    if not atomic_write_json(cfg_path, cfg):
        sys.exit(3)

    print(f"[ok] Chapter incremented: {old} -> {cfg['download']['chapter']}")


if __name__ == "__main__":
    main()
