#!/usr/bin/env python3
"""mangaeasy.utils.fix_name — sanitize the 'name' field in config.json."""

import json
import re
import unicodedata

from mangaeasy.config import CONFIG_FILE


def sanitize_filename(s: str, replacement: str = "_") -> str:
    if not isinstance(s, str):
        s = str(s)
    s = s.strip()
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', replacement, s)
    s = re.sub(rf"{re.escape(replacement)}+", replacement, s)
    s = s.strip(" .")
    if not s:
        s = "untitled"
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", s):
        s = "_" + s
    return s


def main() -> None:
    if not CONFIG_FILE.exists():
        print(f"[ERROR] config.json not found: {CONFIG_FILE}")
        return
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    old = cfg["download"]["name"]
    new = sanitize_filename(old)
    cfg["download"]["name"] = new
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)
    if old != new:
        print(f"[OK] Name sanitized: '{old}' -> '{new}'")
    else:
        print(f"[OK] Name already clean: '{old}'")


if __name__ == "__main__":
    main()
