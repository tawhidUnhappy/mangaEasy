"""mangaeasy.panels.style_detect — webtoon vs paged-manga heuristic.

``mangaeasy style-detect`` answers the first question of the crop stage:
is this series a vertical-strip webtoon (→ ``webtoon-split``) or a paged
manga (→ ``page-split``)? It measures the raw downloaded page images —
tall strips (height ≫ width) mean webtoon, page-shaped images mean paged —
and reports a verdict plus the evidence, machine-readable with ``--json``.

The verdict is a *recommendation*: an agent should still open the returned
``sample_images`` and visually confirm before cropping (some series mix
formats, and "uncertain" is a real answer). Only image headers are read, so
this is fast even on a 100-chapter library.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from PIL import Image

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})

# height/width above this = a vertical strip segment (webtoon slice).
WEBTOON_RATIO = 2.0
# height/width inside this band = a classic manga page (incl. spreads margin).
PAGE_RATIO_LO, PAGE_RATIO_HI = 1.15, 1.85


def measure_item(source_dir: Path) -> dict | None:
    """Aspect-ratio stats for one item's raw pages (header reads only)."""
    ratios: list[float] = []
    paths = sorted(p for p in source_dir.iterdir() if p.suffix.lower() in _IMAGE_EXTS)
    for path in paths:
        try:
            with Image.open(path) as im:
                w, h = im.size
        except Exception:
            continue
        if w > 0:
            ratios.append(h / w)
    if not ratios:
        return None
    tall = sum(1 for r in ratios if r >= WEBTOON_RATIO)
    paged = sum(1 for r in ratios if PAGE_RATIO_LO <= r <= PAGE_RATIO_HI)
    n = len(ratios)
    samples = [paths[0], paths[n // 2], paths[-1]] if n >= 3 else paths
    return {
        "images": n,
        "median_ratio": round(statistics.median(ratios), 2),
        "tall_fraction": round(tall / n, 2),
        "paged_fraction": round(paged / n, 2),
        "sample_images": [str(p) for p in dict.fromkeys(samples)],
    }


def verdict_from_stats(stats: dict) -> str:
    if stats["median_ratio"] >= WEBTOON_RATIO or stats["tall_fraction"] >= 0.6:
        return "webtoon"
    if stats["paged_fraction"] >= 0.6:
        return "paged"
    return "uncertain"


RECOMMENDED_COMMAND = {
    "webtoon": "webtoon-split",
    "paged": "page-split",
    "uncertain": None,
}


def main() -> int:
    from mangaeasy.video_pipeline.common import DEFAULT_PROJECT_ROOT, item_dirs, merge_item_selection

    parser = argparse.ArgumentParser(
        prog="mangaeasy style-detect",
        description="Detect whether a series is a webtoon (vertical strips -> "
                    "webtoon-split) or paged manga (-> page-split) from the "
                    "downloaded page dimensions.",
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT,
                        help="Project folder containing item subfolders (e.g. library/<name>).")
    parser.add_argument("--items", nargs="*", help="Item folders, e.g. 01 02 05-08 (default: all).")
    parser.add_argument("--item-range", help="Inclusive item range, e.g. 01-19.")
    parser.add_argument("--source-subdir", default="download",
                        help="Subfolder inside each item with the raw pages (default: download).")
    parser.add_argument("--json", action="store_true", help="Emit one JSON object on stdout.")
    args = parser.parse_args()

    selection = merge_item_selection(args.items, args.item_range)
    selected = item_dirs(Path(args.project_root), selection)
    per_item: dict[str, dict] = {}
    all_ratios_weighted: list[tuple[float, int]] = []
    for item_dir in selected:
        stats = measure_item(item_dir / args.source_subdir)
        if stats is None:
            continue
        stats["verdict"] = verdict_from_stats(stats)
        per_item[item_dir.name] = stats
        all_ratios_weighted.append((stats["median_ratio"], stats["images"]))

    if not per_item:
        message = f"no images found under {args.project_root}/<item>/{args.source_subdir}/"
        if args.json:
            print(json.dumps({"verdict": None, "error": message, "items": {}}))
        else:
            print(f"[ERROR] {message}")
        return 1

    votes = [s["verdict"] for s in per_item.values()]
    if votes.count("webtoon") > len(votes) / 2:
        overall = "webtoon"
    elif votes.count("paged") > len(votes) / 2:
        overall = "paged"
    else:
        overall = "uncertain"

    # Up to three sample pages spread across the series for visual confirmation.
    sample_pool = [s["sample_images"][len(s["sample_images"]) // 2] for s in per_item.values()]
    n = len(sample_pool)
    overall_samples = list(dict.fromkeys(
        [sample_pool[0], sample_pool[n // 2], sample_pool[-1]] if n >= 3 else sample_pool))

    result = {
        "verdict": overall,
        "recommended_command": RECOMMENDED_COMMAND[overall],
        "items_measured": len(per_item),
        "sample_images": overall_samples,
        "items": per_item,
        "note": "Visually confirm sample_images before cropping; 'uncertain' "
                "or mixed per-item verdicts need a human/LLM look at the pages.",
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0

    print(f"style-detect: {args.project_root} ({len(per_item)} item(s))\n")
    for name, stats in per_item.items():
        print(f"  {name}: {stats['verdict']:9s} median h/w {stats['median_ratio']:5.2f}  "
              f"tall {stats['tall_fraction']:.0%}  paged {stats['paged_fraction']:.0%}  "
              f"({stats['images']} images)")
    print(f"\nOverall: {overall}"
          + (f"  ->  mangaeasy {RECOMMENDED_COMMAND[overall]}" if RECOMMENDED_COMMAND[overall] else
             "  ->  inspect the pages before choosing a splitter"))
    print("Confirm visually: " + ", ".join(overall_samples))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
