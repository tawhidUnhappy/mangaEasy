#!/usr/bin/env python3
"""mangaeasy.video.timestamps — write YouTube chapter timestamps for the joined video.

Mirrors the chapter-collection logic from mangaeasy.video.join.main() so the
timestamps match the actual video precisely.  Output goes to ./tmp/<video-stem>.txt.
"""

import sys
from pathlib import Path

from mangaeasy.config import PROJECT_ROOT, load_download_config, load_system_config
from mangaeasy.paths import manga_dir
from mangaeasy.video.join import _sorted_chapter_dirs
from mangaeasy.video.render import collect_pairs, ffprobe_duration

TMP_DIR = PROJECT_ROOT / "tmp"


def _fmt(seconds: float) -> str:
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def main() -> None:
    syscfg = load_system_config()
    dl = load_download_config()
    name = str(dl["name"])
    manga_root = manga_dir(name)

    if not manga_root.exists():
        print(f"[ERROR] Manga folder not found: {manga_root}")
        sys.exit(1)

    path_cfg = syscfg.get("paths", {})
    processed_subdir = path_cfg.get("processed_subdir", "panels_processed")
    panels_subdir = path_cfg.get("panels_subdir", "panels")
    audio_subdir = path_cfg.get("audio_subdir", "audio")
    upscale_on = bool(syscfg.get("process_panels", {}).get("upscale", True))

    ch_dirs = _sorted_chapter_dirs(manga_root)
    if not ch_dirs:
        print("[ERROR] No chapter directories found.")
        sys.exit(1)

    timestamps: list[tuple[float, str]] = []
    current = 0.0

    for ch_dir in ch_dirs:
        proc_dir = ch_dir / processed_subdir
        raw_dir = ch_dir / panels_subdir
        faded_dir = ch_dir / "audio_faded"
        raw_audio = ch_dir / audio_subdir

        image_root = (
            proc_dir
            if upscale_on and proc_dir.exists() and any(proc_dir.iterdir())
            else raw_dir
        )
        audio_root = (
            faded_dir
            if faded_dir.exists() and any(faded_dir.iterdir())
            else raw_audio
        )

        if not image_root.exists() or not audio_root.exists():
            print(f"[WARN] Chapter {ch_dir.name}: missing panels or audio, skipping.")
            continue

        pairs = collect_pairs(image_root, audio_root)
        if not pairs:
            print(f"[WARN] Chapter {ch_dir.name}: no matched pairs, skipping.")
            continue

        timestamps.append((current, ch_dir.name))
        current += sum(ffprobe_duration(audio) for _, audio in pairs)
        print(f"[INFO] Chapter {ch_dir.name}: starts at {_fmt(timestamps[-1][0])}")

    if not timestamps:
        print("[ERROR] No chapters with data found.")
        sys.exit(1)

    start = ch_dirs[0].name
    end = ch_dirs[-1].name
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    txt_file = TMP_DIR / f"{start}_{end}_{name}_bgm.txt"

    lines = [f"{_fmt(t)} – Chapter {label}" for t, label in timestamps]
    txt_file.write_text("\n".join(lines), encoding="utf-8")

    print(f"\n[DONE] {txt_file}")
    for line in lines:
        print(f"  {line}")


if __name__ == "__main__":
    main()
