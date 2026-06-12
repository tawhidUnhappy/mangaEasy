"""mangaeasy.narration.join — concatenate all chapter narration.json files into one.

Collects every narration.json under library/{name}/ in chapter order and writes
them as a single flat array to library/{name}/narration_all.json.

Each entry keeps its original {image, narration} fields; a chapter field is
added so entries remain traceable after merging.

Usage:
  mangaeasy join-narration            # join all chapters for manga in config.json
  mangaeasy join-narration --dry-run  # print stats without writing
"""

import argparse
import json
from pathlib import Path

from mangaeasy.config import PROJECT_ROOT, load_download_config
from mangaeasy.paths import manga_dir


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _sorted_narration_files(name: str) -> list[tuple[int, Path]]:
    title_dir = manga_dir(name)
    results: list[tuple[int, Path]] = []
    for ch_dir in sorted(title_dir.iterdir()):
        try:
            chapter = int(ch_dir.name)
        except ValueError:
            continue
        path = ch_dir / f"narration_{chapter:02d}.json"
        if path.exists():
            results.append((chapter, path))
    return sorted(results)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print per-chapter stats without writing the output file.")
    args = parser.parse_args()

    dl   = load_download_config()
    name = str(dl["name"])

    files = _sorted_narration_files(name)
    if not files:
        print(f"[ERROR] No narration.json files found for {name!r}")
        return

    combined: list[dict] = []
    total = 0
    for chapter, path in files:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            print(f"  [SKIP] ch{chapter:02d}  (empty file)")
            continue
        entries = json.loads(raw)
        combined.append({"chapter": chapter, "panels": entries})
        total += len(entries)
        print(f"  ch{chapter:02d}  {len(entries):>4} entries  →  {path}")

    print(f"\nTotal: {total} entries across {len(combined)} chapter(s).")

    if args.dry_run:
        print("[DRY-RUN] Nothing written.  Remove --dry-run to save the file.")
        return

    chapters = [ch for ch, _ in files]
    start, end = chapters[0], chapters[-1]
    out_dir  = PROJECT_ROOT / "tmp"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{start:02d}_{end:02d}_{name}.json"
    out_path.write_text(
        json.dumps(combined, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[WROTE] {out_path}")


if __name__ == "__main__":
    main()
