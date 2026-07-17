"""mediaconductor.panels.style_detect — webtoon vs paged-manga heuristic.

``mediaconductor style-detect`` answers the first question of the crop stage:
is this series a vertical-strip webtoon (→ ``webtoon-split``) or a paged
manga (→ ``page-split``)? It measures the raw downloaded page images —
tall strips (height ≫ width) mean webtoon, page-shaped images mean paged —
and reports a verdict plus the evidence, machine-readable with ``--json``.
Page-shaped images that share one width but vary wildly in height are
recognized as a webtoon pre-sliced into chunks by the host (see
``_looks_sliced``) — the case that used to misread as "paged".

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

from mediaconductor.brand import CLI_NAME
from mediaconductor.path_safety import relative_subpath_arg

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})

# height/width above this = a vertical strip segment (webtoon slice).
WEBTOON_RATIO = 2.0
# height/width inside this band = a classic manga page (incl. spreads margin).
PAGE_RATIO_LO, PAGE_RATIO_HI = 1.15, 1.85
# Sliced-webtoon detection: hosts (MangaDex included) often serve webtoons
# pre-cut into page-height chunks — page-shaped ratios, so the ratio bands
# alone misread them as paged manga (a real production incident: the paged
# splitter ran MAGI over a webtoon). Real page *scans* have near-identical
# heights; webtoon slices share one width but vary wildly in height because
# each cut lands wherever the panel flow allowed.
SLICE_WIDTH_UNIFORM_MIN = 0.9   # fraction of images at the modal width
SLICE_HEIGHT_CV_MIN = 0.08      # height stdev/mean among modal-width images
SLICE_MIN_IMAGES = 8            # too few images -> not enough evidence
PAGED_HEIGHT_CV_MAX = 0.08      # above this, "paged" confidence drops to uncertain


def measure_item(source_dir: Path) -> dict | None:
    """Dimension stats for one item's raw pages (header reads only)."""
    sizes: list[tuple[int, int]] = []
    paths = sorted(p for p in source_dir.iterdir() if p.suffix.lower() in _IMAGE_EXTS)
    for path in paths:
        try:
            with Image.open(path) as im:
                w, h = im.size
        except Exception:
            continue
        if w > 0 and h > 0:
            sizes.append((w, h))
    if not sizes:
        return None
    ratios = [h / w for w, h in sizes]
    tall = sum(1 for r in ratios if r >= WEBTOON_RATIO)
    paged = sum(1 for r in ratios if PAGE_RATIO_LO <= r <= PAGE_RATIO_HI)
    n = len(ratios)
    widths = [w for w, _ in sizes]
    modal_width = statistics.mode(widths)
    modal_heights = [h for w, h in sizes if w == modal_width]
    height_cv = (
        statistics.pstdev(modal_heights) / statistics.mean(modal_heights)
        if len(modal_heights) >= 2 else 0.0
    )
    samples = [paths[0], paths[n // 2], paths[-1]] if n >= 3 else paths
    return {
        "images": n,
        "median_ratio": round(statistics.median(ratios), 2),
        "tall_fraction": round(tall / n, 2),
        "paged_fraction": round(paged / n, 2),
        "width_uniform_fraction": round(widths.count(modal_width) / n, 2),
        "height_cv": round(height_cv, 3),
        "sample_images": [str(p) for p in dict.fromkeys(samples)],
    }


def _looks_sliced(stats: dict) -> bool:
    """Page-shaped chunks that are really a pre-cut vertical strip."""
    return (
        stats["images"] >= SLICE_MIN_IMAGES
        and stats.get("width_uniform_fraction", 0.0) >= SLICE_WIDTH_UNIFORM_MIN
        and stats.get("height_cv", 0.0) >= SLICE_HEIGHT_CV_MIN
    )


def verdict_from_stats(stats: dict) -> str:
    if stats["median_ratio"] >= WEBTOON_RATIO or stats["tall_fraction"] >= 0.6:
        return "webtoon"
    if _looks_sliced(stats):
        return "webtoon"
    if stats["paged_fraction"] >= 0.6:
        return "paged" if stats.get("height_cv", 0.0) < PAGED_HEIGHT_CV_MAX else "uncertain"
    return "uncertain"


RECOMMENDED_COMMAND = {
    "webtoon": "webtoon-split",
    "paged": "page-split",
    "uncertain": None,
}


def style_guard(source_dir: Path, expected: str) -> tuple[bool, str]:
    """Pre-flight check the splitters run before cropping an item.

    ``expected`` is the style the invoking command handles ("webtoon" for
    ``webtoon-split``, "paged" for ``page-split``). Returns ``(ok, message)``:
    ``ok`` is False only on a confident opposite verdict — running the paged
    splitter on a vertical strip (or vice versa) never produces usable panels,
    and in production a small agent burned a full crop+narration pass on
    exactly that mistake before a human caught it. "uncertain" and unreadable
    sources stay allowed (the verify sheets catch those), and ``--force-style``
    on the splitters bypasses the guard for deliberate mixed-format items.
    """
    opposite = {"webtoon": "paged", "paged": "webtoon"}[expected]
    stats = measure_item(source_dir)
    if stats is None:
        return True, "style guard: no readable pages to measure"
    verdict = verdict_from_stats(stats)
    detail = (f"median h/w {stats['median_ratio']}, tall {stats['tall_fraction']:.0%}, "
              f"paged {stats['paged_fraction']:.0%}, height-cv {stats['height_cv']} "
              f"over {stats['images']} image(s)")
    if verdict == opposite:
        return False, (
            f"style guard: these pages measure as {opposite.upper()} ({detail}). "
            f"Use `{CLI_NAME} {RECOMMENDED_COMMAND[opposite]}` instead, or pass "
            f"--force-style if this specific item really is {expected}. "
            f"Confirm visually: {', '.join(stats['sample_images'])}"
        )
    if verdict == "uncertain":
        return True, (
            f"style guard: uncertain format ({detail}) — proceeding, but visually "
            f"confirm the verify sheets extra carefully: {', '.join(stats['sample_images'])}"
        )
    return True, f"style guard: confirmed {verdict} ({detail})"


def main() -> int:
    from mediaconductor.video_pipeline.common import DEFAULT_PROJECT_ROOT, item_dirs, merge_item_selection

    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} style-detect",
        description="Detect whether a series is a webtoon (vertical strips -> "
                    "webtoon-split) or paged manga (-> page-split) from the "
                    "downloaded page dimensions.",
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT,
                        help="Project folder containing item subfolders (e.g. library/<name>).")
    parser.add_argument("--items", nargs="*", help="Item folders, e.g. 01 02 05-08 (default: all).")
    parser.add_argument("--item-range", help="Inclusive item range, e.g. 01-19.")
    parser.add_argument("--source-subdir", type=relative_subpath_arg, default="download",
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
          + (f"  ->  {CLI_NAME} {RECOMMENDED_COMMAND[overall]}" if RECOMMENDED_COMMAND[overall] else
             "  ->  inspect the pages before choosing a splitter"))
    print("Confirm visually: " + ", ".join(overall_samples))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
