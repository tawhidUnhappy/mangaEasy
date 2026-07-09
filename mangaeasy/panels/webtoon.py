"""mangaeasy.panels.webtoon
Item-pipeline webtoon splitter with verification output (`mangaeasy webtoon-split`).

Builds on mangaeasy.panels.gutter (same detection code path as `gutter-split`)
and adds the production hardening that recap sessions kept needing:

- auto-split of merged mega-panels (taller than --max-ratio x width) at the
  quietest row near even split points, so one missed gutter doesn't produce a
  10,000-px "panel" that renders unreadably in a video
- rescue of dropped gutter gaps that actually contain content — scene-break
  captions ("ONE HOUR LATER...") often sit in otherwise-gutter-colored gaps
  and would silently vanish from the story
- per-item verification artifacts for human/AI review before narration:
  numbered contact sheets and a downscaled strip overlay (green = kept panel,
  blue = auto-cut line, red = dropped rows)
- a per-item report line (suspects / rescued / content_drops) plus the
  standard MANGAEASY_PROGRESS / MANGAEASY_RESULT markers

Every flagged suspect and content_drop should be visually cleared against the
verify images before writing narration — known-benign patterns are scanlator
credit banners and end-of-chapter recruiting notices, but the flags exist
because sometimes it *is* story content.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from mangaeasy.panels.gutter import (
    GutterConfig,
    _recursive_ranges,
    collect_image_paths,
    load_gutter_config,
    stitch_images,
)
from mangaeasy.utils import emit_result, next_archive_run_dir

Image.MAX_IMAGE_PIXELS = None

Range = Tuple[int, int]

# Battle-tested defaults from production recap runs (see docs/recap-video-playbook.md).
DEFAULT_MAX_RATIO = 2.2        # panels taller than this * width get auto-split
DEFAULT_TARGET_HEIGHT = 1300   # aim for segments around this height
DEFAULT_MIN_SEGMENT = 520      # never create a segment shorter than this
DEFAULT_CUT_WINDOW = 380       # search +/- this around even split points
DEFAULT_ENERGY_THRESHOLD = 22.0  # row-std above this counts as "real content"
MIN_RESCUE_GAP = 40            # gaps shorter than this are plain gutters
MAX_RESCUE_GAP = 700           # gaps taller than this are handled as drops to review


def row_energy(combined: Image.Image) -> Tuple[np.ndarray, np.ndarray]:
    """Return (smoothed, raw) per-row 'busyness': horizontal std of grayscale.

    Smoothed is a +/-12-row rolling max, used to pick quiet rows for auto-cut
    placement. Raw is used to judge whether a dropped gap contains content.
    """
    gray = np.array(combined.convert("L"), dtype=np.float32)[:, ::3]
    std = gray.std(axis=1)
    k = 12
    pad = np.pad(std, k, mode="edge")
    smoothed = np.array([pad[i:i + 2 * k + 1].max() for i in range(len(std))], dtype=np.float32)
    return smoothed, std.astype(np.float32)


def rescue_gaps(
    ranges: List[Range],
    raw_std: np.ndarray,
    *,
    energy_threshold: float = DEFAULT_ENERGY_THRESHOLD,
    min_gap: int = MIN_RESCUE_GAP,
    max_gap: int = MAX_RESCUE_GAP,
) -> Tuple[List[Range], List[str]]:
    """Attach dropped gaps that contain real content to the following panel.

    Gutter detection drops anything that scores as "gutter colored", which can
    swallow short caption strips between panels. A gap whose interior rows show
    energy above the threshold is merged into the next panel so no story text
    is lost. Returns (new_ranges, human-readable rescue notes).
    """
    if not ranges:
        return ranges, []
    rescued: List[str] = []
    out = list(ranges)
    gaps = [(0, out[0][0], 0)] + [
        (out[i][1], out[i + 1][0], i + 1) for i in range(len(out) - 1)
    ]
    for gap_top, gap_bottom, next_idx in gaps:
        if not (min_gap < gap_bottom - gap_top <= max_gap):
            continue
        interior = raw_std[gap_top + 15: gap_bottom - 15]
        if interior.size and float(interior.max()) > energy_threshold:
            _, bottom = out[next_idx]
            out[next_idx] = (gap_top, bottom)
            rescued.append(f"y{gap_top}-{gap_bottom}->panel{next_idx + 1}")
    return out, rescued


def auto_split_ranges(
    ranges: List[Range],
    energy: np.ndarray,
    width: int,
    *,
    max_ratio: float = DEFAULT_MAX_RATIO,
    target_height: int = DEFAULT_TARGET_HEIGHT,
    min_segment: int = DEFAULT_MIN_SEGMENT,
    window: int = DEFAULT_CUT_WINDOW,
) -> Tuple[List[Range], List[int]]:
    """Split ranges taller than max_ratio * width at low-energy rows.

    Cuts are placed near even split points but snapped to the quietest row
    within +/-window, so they land in gutters/sky rather than through faces
    or text. Returns (new_ranges, cut_row_ys) — cut rows feed the overlay.
    """
    out: List[Range] = []
    cut_rows: List[int] = []
    for top, bottom in ranges:
        height = bottom - top
        if height <= max_ratio * width:
            out.append((top, bottom))
            continue
        n = max(2, round(height / target_height))
        cuts: List[int] = []
        prev = top
        for k in range(1, n):
            target_y = top + height * k // n
            lo = max(prev + min_segment, target_y - window)
            hi = min(bottom - min_segment, target_y + window)
            if lo >= hi:
                continue
            y = lo + int(np.argmin(energy[lo:hi]))
            cuts.append(y)
            prev = y
        segments = [top] + cuts + [bottom]
        for seg_top, seg_bottom in zip(segments, segments[1:], strict=False):
            out.append((seg_top, seg_bottom))
        cut_rows.extend(cuts)
    return out, cut_rows


def apply_range_overrides(
    ranges: List[Range],
    overrides: Dict | None,
    total_height: int,
    *,
    min_height: int = 20,
) -> List[Range]:
    """Apply manual per-item corrections after automatic detection.

    Supported keys (all optional):
      "replace":  [[top, bottom], ...]   discard detection, use these ranges
      "merge":    [[i, j], ...]          merge detected ranges i..j (0-based, inclusive)
      "split_at": [y, ...]               force an extra cut at stitched-strip y
    """
    if not overrides:
        return ranges
    if "replace" in overrides:
        ranges = [tuple(r) for r in overrides["replace"]]
    if "merge" in overrides:
        for i, j in sorted(overrides["merge"], reverse=True):
            ranges = ranges[:i] + [(ranges[i][0], ranges[j][1])] + ranges[j + 1:]
    if "split_at" in overrides:
        for y in overrides["split_at"]:
            split: List[Range] = []
            for top, bottom in ranges:
                if top < y < bottom:
                    split.extend([(top, y), (y, bottom)])
                else:
                    split.append((top, bottom))
            ranges = split
    ranges = [
        (max(0, top), min(total_height, bottom))
        for top, bottom in ranges
        if bottom - top >= min_height
    ]
    return sorted(ranges, key=lambda r: r[0])


def find_content_gaps(
    ranges: List[Range],
    raw_std: np.ndarray,
    total_height: int,
    *,
    energy_threshold: float = DEFAULT_ENERGY_THRESHOLD,
    min_gap: int = MIN_RESCUE_GAP,
) -> List[str]:
    """Report dropped regions whose interior still looks like content.

    These are the rows nothing rescued or covered — each entry needs a human
    look at the strip overlay (trailing scanlator notices are the usual benign
    match, at a consistent height/energy signature per group).
    """
    if not ranges:
        return []
    drops: List[str] = []
    edges = (
        [(0, ranges[0][0])]
        + [(a[1], b[0]) for a, b in zip(ranges, ranges[1:], strict=False)]
        + [(ranges[-1][1], total_height)]
    )
    for gap_top, gap_bottom in edges:
        if gap_bottom - gap_top < min_gap:
            continue
        interior = raw_std[gap_top + 15: max(gap_top + 16, gap_bottom - 15)]
        energy = float(interior.max()) if interior.size else 0.0
        if energy > energy_threshold:
            drops.append(f"y{gap_top}-{gap_bottom} (h={gap_bottom - gap_top}, e={energy:.0f})")
    return drops


# ---------------------------------------------------------------------------
# Verification artifacts
# ---------------------------------------------------------------------------

def _load_fonts() -> Tuple[ImageFont.ImageFont, ImageFont.ImageFont]:
    try:
        return (ImageFont.truetype("arialbd.ttf", 28), ImageFont.truetype("arialbd.ttf", 44))
    except Exception:
        default = ImageFont.load_default()
        return default, default


def write_contact_sheets(
    item: str,
    crops: Sequence[Tuple[int, Image.Image]],
    verify_dir: Path,
    *,
    suspect_ratio: float = 2.4,
    suspect_min_height: int = 140,
) -> int:
    """Numbered thumbnail grid per item; suspects get a red '!!' label."""
    font, _ = _load_fonts()
    thumb_w, thumb_h, cols, pad = 240, 340, 7, 10
    label_h = 36
    per_sheet = cols * 6
    sheets = 0
    for start in range(0, len(crops), per_sheet):
        chunk = crops[start:start + per_sheet]
        rows = (len(chunk) + cols - 1) // cols
        sheet = Image.new(
            "RGB",
            (cols * (thumb_w + pad) + pad, rows * (thumb_h + label_h + pad) + pad),
            (30, 30, 30),
        )
        draw = ImageDraw.Draw(sheet)
        for k, (idx, im) in enumerate(chunk):
            row, col = divmod(k, cols)
            x = pad + col * (thumb_w + pad)
            y = pad + row * (thumb_h + label_h + pad)
            thumb = im.copy()
            thumb.thumbnail((thumb_w, thumb_h))
            sheet.paste(thumb, (x, y + label_h))
            ratio = im.height / im.width
            warn = " !!" if ratio > suspect_ratio or im.height < suspect_min_height else ""
            draw.text(
                (x, y + 2),
                f"#{idx} {im.width}x{im.height}{warn}",
                fill=(255, 80, 80) if warn else (220, 220, 220),
                font=font,
            )
        sheets += 1
        sheet.save(verify_dir / f"{item}_sheet_{sheets}.png")
    return sheets


def write_strip_overlay(
    item: str,
    combined: Image.Image,
    ranges: List[Range],
    verify_dir: Path,
    cut_rows: Sequence[int] = (),
) -> None:
    """Downscaled strip: panel boxes (green), auto-cuts (blue), drops (red)."""
    font, _ = _load_fonts()
    scale = 260 / combined.width
    small = combined.resize((260, max(1, int(combined.height * scale))))
    draw = ImageDraw.Draw(small, "RGBA")
    covered = [(int(t * scale), int(b * scale)) for t, b in ranges]
    last = 0
    for top, bottom in covered:
        if top > last + 1:
            draw.rectangle([0, last, small.width, top], fill=(255, 0, 0, 90))
        draw.rectangle([0, top, small.width - 1, bottom], outline=(0, 255, 0, 255), width=2)
        last = bottom
    if last < small.height - 1:
        draw.rectangle([0, last, small.width, small.height], fill=(255, 0, 0, 90))
    for y in cut_rows:
        draw.line([0, int(y * scale), small.width, int(y * scale)], fill=(0, 120, 255, 255), width=3)
    for k, (top, _bottom) in enumerate(covered, 1):
        draw.text((6, top + 3), str(k), fill=(0, 160, 255), font=font)
    tile_h = 3200
    n = 0
    for y in range(0, small.height, tile_h):
        n += 1
        small.crop((0, y, small.width, min(small.height, y + tile_h))).save(
            verify_dir / f"{item}_strip_{n}.png")


# ---------------------------------------------------------------------------
# Per-item pipeline
# ---------------------------------------------------------------------------

def _archive_existing_panels(panels_dir: Path) -> Path | None:
    """Move a non-empty existing panels dir into <item>/old/run_NNNN/panels."""
    if not panels_dir.exists() or not any(panels_dir.iterdir()):
        if panels_dir.exists():
            panels_dir.rmdir()
        return None
    run_dir = next_archive_run_dir(panels_dir.parent / "old")
    run_dir.mkdir(parents=True, exist_ok=True)
    destination = run_dir / panels_dir.name
    shutil.move(str(panels_dir), str(destination))
    return destination


def process_item(item_dir: Path, args, overrides: Dict, verify_dir: Path) -> Dict:
    item = item_dir.name
    source_dir = item_dir / args.source_subdir
    panels_dir = item_dir / args.panels_subdir
    paths = collect_image_paths(source_dir, sort_mode=args.sort) if source_dir.is_dir() else []
    if not paths:
        print(f"[{item}] SKIP: no images in {source_dir}", flush=True)
        return {"item": item, "status": "skipped"}

    combined = stitch_images(paths)
    cfg = load_gutter_config(Path(args.config)) if args.config else GutterConfig()
    ranges = _recursive_ranges(combined, cfg, args.device)
    energy, raw_std = row_energy(combined)
    ranges, rescued = rescue_gaps(ranges, raw_std, energy_threshold=args.energy_threshold)
    ranges, cut_rows = auto_split_ranges(
        ranges, energy, combined.width,
        max_ratio=args.max_ratio, target_height=args.target_height,
        min_segment=args.min_segment, window=args.cut_window,
    )
    ranges = apply_range_overrides(ranges, overrides.get(item), combined.height)
    if not ranges:
        print(f"[{item}] ERROR: no panel ranges detected", flush=True)
        return {"item": item, "status": "error"}

    archived = _archive_existing_panels(panels_dir)
    panels_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.prefix_template.format(item=item)
    crops: List[Tuple[int, Image.Image]] = []
    suspects: List[str] = []
    for i, (top, bottom) in enumerate(ranges, 1):
        panel = combined.crop((0, top, combined.width, bottom)).convert("RGB")
        panel.save(panels_dir / f"{prefix}{i:03d}.jpg", "JPEG", quality=95, optimize=True)
        crops.append((i, panel))
        ratio = panel.height / panel.width
        if ratio > 2.4 or panel.height < 140:
            suspects.append(f"#{i} {panel.width}x{panel.height}")

    content_drops = find_content_gaps(
        ranges, raw_std, combined.height, energy_threshold=args.energy_threshold)
    write_contact_sheets(item, crops, verify_dir)
    write_strip_overlay(item, combined, ranges, verify_dir, cut_rows)

    dropped = combined.height - sum(b - t for t, b in ranges)
    print(
        f"[{item}] pages={len(paths)} strip_h={combined.height} panels={len(ranges)} "
        f"dropped_rows={dropped} ({100 * dropped / combined.height:.1f}%) "
        f"suspects={suspects if suspects else 'none'} "
        f"rescued={rescued if rescued else 'none'} "
        f"content_drops={content_drops if content_drops else 'none'}"
        + (f" archived_previous={archived}" if archived else ""),
        flush=True,
    )
    return {
        "item": item,
        "status": "ok",
        "panels": len(ranges),
        "suspects": suspects,
        "rescued": rescued,
        "content_drops": content_drops,
        # The exact images an agent must open to clear the flags above.
        "verify_images": sorted(
            str(p) for pattern in (f"{item}_sheet_*.png", f"{item}_strip_*.png")
            for p in verify_dir.glob(pattern)
        ),
    }


def parse_args() -> argparse.Namespace:
    from mangaeasy.video_pipeline.common import DEFAULT_PROJECT_ROOT, DEFAULT_WORK_DIR

    parser = argparse.ArgumentParser(
        description="Split webtoon strips into panels with auto-split, gap rescue "
                    "and verification sheets (item-pipeline layout)."
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT,
                        help="Project folder containing item subfolders (library/<name>).")
    parser.add_argument("--items", nargs="*", help="Item folders, e.g. 01 02 05-08.")
    parser.add_argument("--item-range", help="Inclusive item range, e.g. 01-19.")
    parser.add_argument("--source-subdir", default="download",
                        help="Subfolder inside each item with the raw pages (default: download).")
    parser.add_argument("--panels-subdir", default="panels",
                        help="Subfolder inside each item to write crops to (default: panels).")
    parser.add_argument("--verify-root", type=Path, default=None,
                        help="Where to write verification sheets "
                             "(default: <work-dir>/webtoon_verify/<project-name>).")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--prefix-template", default="ch{item}_",
                        help="Crop filename prefix; '{item}' expands to the item name.")
    parser.add_argument("--config", default=None,
                        help="Optional config.json with UPPER_SNAKE gutter keys (see gutter-split).")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--sort", default="numeric", choices=["numeric", "lex"])
    parser.add_argument("--max-ratio", type=float, default=DEFAULT_MAX_RATIO)
    parser.add_argument("--target-height", type=int, default=DEFAULT_TARGET_HEIGHT)
    parser.add_argument("--min-segment", type=int, default=DEFAULT_MIN_SEGMENT)
    parser.add_argument("--cut-window", type=int, default=DEFAULT_CUT_WINDOW)
    parser.add_argument("--energy-threshold", type=float, default=DEFAULT_ENERGY_THRESHOLD)
    parser.add_argument("--overrides", type=Path, default=None,
                        help="JSON keyed by item name with replace/merge/split_at corrections.")
    return parser.parse_args()


def main() -> int:
    from mangaeasy.video_pipeline.common import item_dirs, merge_item_selection

    args = parse_args()
    project_root = args.project_root.resolve()
    selection = merge_item_selection(args.items, args.item_range)
    selected = item_dirs(project_root, selection)
    if not selected:
        print(f"[FATAL] No item folders found under {project_root}")
        return 1

    overrides: Dict = {}
    if args.overrides and args.overrides.exists():
        overrides = json.loads(args.overrides.read_text(encoding="utf-8"))

    verify_dir = (
        args.verify_root
        if args.verify_root
        else args.work_dir / "webtoon_verify" / project_root.name
    ).resolve()
    verify_dir.mkdir(parents=True, exist_ok=True)

    reports = []
    for i, item_dir in enumerate(selected, 1):
        print(f"MANGAEASY_PROGRESS {i}/{len(selected)}", flush=True)
        reports.append(process_item(item_dir, args, overrides, verify_dir))

    failed = [r["item"] for r in reports if r["status"] == "error"]
    emit_result(
        command="webtoon-split",
        project=project_root.name,
        verify_dir=verify_dir,
        items=reports,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
