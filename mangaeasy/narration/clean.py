#!/usr/bin/env python3
"""mangaeasy.narration.clean — clean narration text for TTS."""

import re
import shutil
import sys
import traceback
import unicodedata

from mangaeasy.narration import load_narration, save_narration
from mangaeasy.paths import narration_json


def clean_text_for_tts(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2014", ", ").replace("\u2013", "-")
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"\(.*?\)", "", text)
    text = text.replace("*", "")
    text = text.replace("?", ".").replace("!", ".")
    text = text.replace("...", ". ").replace("\u2026", ". ")
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text.endswith("'") or text.endswith('"'):
        text = text[:-1].strip()
    if text and text[-1].isalnum():
        text += "."
    if not re.search(r"[a-zA-Z0-9]", text):
        return ""
    return text


def main() -> None:
    print("[INFO] Cleaning narration text for TTS...")
    try:
        narr_path = narration_json()

        if not narr_path.exists():
            print(f"[ERROR] Narration file not found: {narr_path}")
            sys.exit(1)

        backup = narr_path.with_suffix(".json.bak")
        shutil.copy2(narr_path, backup)
        print(f"[INFO] Backup: {backup}")

        narrations = load_narration(narr_path)

        changed = 0
        for item in narrations:
            orig = item.get("narration", "")
            cleaned = clean_text_for_tts(orig)
            if orig != cleaned:
                changed += 1
            item["narration"] = cleaned

        save_narration(narrations, narr_path)

        print(f"[SUCCESS] {changed} entries modified. Saved to {narr_path}")

    except Exception as exc:
        print(f"[FATAL] {exc}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
