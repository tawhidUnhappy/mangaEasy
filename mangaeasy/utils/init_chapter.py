#!/usr/bin/env python3
"""mangaeasy.utils.init_chapter — create empty narration.json + novel.txt for a new chapter."""

from mangaeasy.config import load_download_config
from mangaeasy.paths import chapter_dir


def main() -> None:
    dl      = load_download_config()
    name    = str(dl["name"])
    chapter = int(dl["chapter"])

    ch_dir = chapter_dir(name, chapter)
    ch_dir.mkdir(parents=True, exist_ok=True)

    narration_json = ch_dir / f"narration_{chapter:02d}.json"
    novel_txt      = ch_dir / f"{chapter}-{chapter + 1}_novel.txt"

    narration_json.write_text("", encoding="utf-8")
    novel_txt.write_text("", encoding="utf-8")

    print(f"[DONE] Created in {ch_dir}:")
    print(f"       {narration_json.name}")
    print(f"       {novel_txt.name}")

    input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()
