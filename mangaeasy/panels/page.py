"""mangaeasy.panels.page
Item-pipeline paged-manga splitter with verification output (`mediaconductor page-split`).

This is the paged-manga counterpart to `webtoon-split`. Where `webtoon-split`
finds gutters in one tall vertical strip, `page-split` runs **MAGI v3** panel
detection on each page, sorts the boxes into manga reading order, crops them,
and — like the webtoon splitter — writes verification artifacts so a human or
AI can clear every page before writing narration.

It exists to retire the copy-paste scratch scripts that used to live inside
docs/recap-video-playbook.md (Phases 2–3). MAGI is never fully trusted: in the
reference production it was wrong on ~4 of 61 pages (whole-page boxes, merged
panels, a missed column), so the verification overlays and the `--overrides`
escape hatch are load-bearing, not optional.

Pipeline per item:
  1. list pages in <item>/<source-subdir> (default: download/) in reading order
  2. run MAGI v3 once over the whole item in the external magi-v3 tool env
     (via assets/tools/batch_detect_magi.py) -> a detections.json
  3. per page: take MAGI's boxes (or a per-page override), clamp, sort into
     reading order, crop, save as <panels-subdir>/<item>_<page>_<panel>.jpg
  4. write verification images: a numbered box overlay per page + a contact
     sheet of every crop, plus the raw detections.json for crafting overrides
  5. print a per-item report (panels / suspect pages) + the standard
     MANGAEASY_PROGRESS / MANGAEASY_RESULT markers

Suspect pages (always eyeball these against the overlay before narration):
  * a page where MAGI found no panels -> the whole page is used as one crop
  * a page whose single box covers most of the sheet -> likely a missed split

Fix a bad page with --overrides: a JSON file keyed by the page's filename whose
value is a list of [x1, y1, x2, y2] pixel boxes that fully replace MAGI's boxes
for that page, e.g. {"01_09.jpg": [[0, 0, 900, 700], [0, 700, 900, 1400]]}.
Overlapping override boxes are fine and often correct (diagonal borders).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from mangaeasy.brand import CLI_NAME
from mangaeasy.panels.ai import _clamp_box, _manga_reading_order
from mangaeasy.panels.gutter import collect_image_paths
from mangaeasy.panels.webtoon import _archive_existing_panels, write_contact_sheets
from mangaeasy.tools.external import python_command, resolve_tool_dir, tool_env
from mangaeasy.utils import emit_result

Image.MAX_IMAGE_PIXELS = None

Box = Dict[str, int]

# A single detected box covering at least this fraction of the page area is
# almost always MAGI returning the whole page instead of splitting it.
FULL_PAGE_AREA_FRAC = 0.85

# Where the shipped batch-detect adapter lives inside the package (fallback if
# it was not copied into the tool env, e.g. an env installed before it shipped).
_PACKAGED_BATCH_SCRIPT = (
    Path(__file__).resolve().parents[1] / "assets" / "tools" / "batch_detect_magi.py"
)


def _load_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arialbd.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _resolve_batch_script(magi_dir: Path) -> Optional[Path]:
    installed = magi_dir / "batch_detect_magi.py"
    if installed.exists():
        return installed
    if _PACKAGED_BATCH_SCRIPT.exists():
        return _PACKAGED_BATCH_SCRIPT
    return None


def run_batch_detect(
    pages_dir: Path, out_path: Path, *, device: str = "auto", dtype: str = "auto"
) -> Optional[Dict[str, dict]]:
    """Run MAGI v3 over every page in `pages_dir`, streaming progress.

    Returns the parsed detections mapping {page_name: {size, panels}} or None
    if the tool env / model run was unavailable.
    """
    magi_dir = resolve_tool_dir("magi-v3", required=False)
    if magi_dir is None:
        print(
            "[page-split] MAGI v3 tool env not found. Install it with "
            f"`{CLI_NAME} install-tool magi-v3`.",
            flush=True,
        )
        return None
    script = _resolve_batch_script(magi_dir)
    if script is None:
        print("[page-split] batch_detect_magi.py missing (reinstall magi-v3).", flush=True)
        return None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        *python_command(magi_dir),
        str(script),
        str(pages_dir),
        "--out", str(out_path),
        "--device", device,
        "--dtype", dtype,
    ]
    proc = subprocess.Popen(
        cmd, cwd=magi_dir, env=tool_env(),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line.rstrip(), flush=True)
    code = proc.wait()
    if code != 0 or not out_path.exists():
        print(f"[page-split] MAGI batch detection failed (exit {code}).", flush=True)
        return None
    try:
        return json.loads(out_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[page-split] could not read detections: {exc}", flush=True)
        return None


def boxes_for_page(
    detection: dict | None, override: Sequence | None, width: int, height: int
) -> Tuple[List[Box], bool]:
    """Resolve, clamp and reading-order the boxes for one page.

    Returns (boxes, is_full_page_fallback). If nothing usable is found the
    whole page becomes a single box and the flag is True.
    """
    raw = list(override) if override is not None else list((detection or {}).get("panels", []))
    boxes = [b for entry in raw if (b := _clamp_box(entry, width, height))]
    if not boxes:
        return [{"x1": 0, "y1": 0, "x2": width, "y2": height}], True
    return boxes, False


def write_page_overlay(
    page_img: Image.Image, boxes: List[Box], dest: Path, *, max_side: int = 1000
) -> None:
    """Save a downscaled copy of the page with numbered red panel boxes."""
    overlay = page_img.convert("RGB").copy()
    draw = ImageDraw.Draw(overlay)
    font = _load_font(max(28, overlay.width // 22))
    for k, b in enumerate(boxes, 1):
        draw.rectangle([b["x1"], b["y1"], b["x2"], b["y2"]], outline=(255, 40, 40), width=max(4, overlay.width // 220))
        draw.text(
            (b["x1"] + 12, b["y1"] + 8), str(k), fill=(255, 40, 40), font=font,
            stroke_width=3, stroke_fill=(255, 255, 255),
        )
    overlay.thumbnail((max_side, max_side))
    dest.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(dest)


def process_item(item_dir: Path, args, overrides: Dict, verify_dir: Path) -> Dict:
    item = item_dir.name
    source_dir = item_dir / args.source_subdir
    panels_dir = item_dir / args.panels_subdir
    paths = collect_image_paths(source_dir, sort_mode=args.sort) if source_dir.is_dir() else []
    if not paths:
        print(f"[{item}] SKIP: no images in {source_dir}", flush=True)
        return {"item": item, "status": "skipped"}

    item_verify = verify_dir / item
    item_verify.mkdir(parents=True, exist_ok=True)
    detections = run_batch_detect(
        source_dir, item_verify / f"{item}_detections.json",
        device=args.device, dtype=args.dtype,
    )
    if detections is None:
        return {"item": item, "status": "error", "reason": "detection_failed"}

    rtl = None if args.reading_direction == "auto" else (args.reading_direction == "rtl")
    item_overrides = overrides.get(item, {})

    archived = _archive_existing_panels(panels_dir)
    panels_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.prefix_template.format(item=item)

    crops: List[Tuple[int, Image.Image]] = []
    suspects: List[str] = []
    full_page_boxes: List[str] = []
    total_panels = 0
    crop_index = 0
    for page_no, page_path in enumerate(paths, 1):
        img = Image.open(page_path).convert("RGB")
        W, H = img.size
        override = item_overrides.get(page_path.name)
        boxes, full_page = boxes_for_page(detections.get(page_path.name), override, W, H)
        boxes = _manga_reading_order(boxes, rtl=rtl)

        if full_page and override is None:
            suspects.append(f"{page_path.name} no-panels")
        elif len(boxes) == 1 and override is None:
            b = boxes[0]
            if (b["x2"] - b["x1"]) * (b["y2"] - b["y1"]) >= FULL_PAGE_AREA_FRAC * W * H:
                # Informational, NOT a suspect: one box covering the page is
                # the normal shape of splash art, chapter titles, and credits
                # pages. Flagging it fired on 11/11 chapters of a real
                # production (100% benign) — alarm fatigue that buried the
                # suspects an agent actually must act on.
                full_page_boxes.append(f"{page_path.name} full-page-box")

        for panel_no, b in enumerate(boxes, 1):
            crop = img.crop((b["x1"], b["y1"], b["x2"], b["y2"]))
            name = f"{prefix}{page_no:03d}_{panel_no:02d}.jpg"
            crop.save(panels_dir / name, "JPEG", quality=95, optimize=True)
            crop_index += 1
            crops.append((crop_index, crop))
            total_panels += 1

        write_page_overlay(img, boxes, item_verify / f"{item}_page_{page_no:03d}.png")

    write_contact_sheets(item, crops, item_verify)

    print(
        f"[{item}] pages={len(paths)} panels={total_panels} "
        f"suspects={suspects if suspects else 'none'}"
        + (f" full_page_boxes={len(full_page_boxes)}" if full_page_boxes else "")
        + (f" archived_previous={archived}" if archived else ""),
        flush=True,
    )
    return {
        "item": item,
        "status": "ok",
        "pages": len(paths),
        "panels": total_panels,
        "suspects": suspects,
        # Single-box full pages (splash art / titles / credits) — expected,
        # listed for completeness; the verify sheets confirm at a glance.
        "full_page_boxes": full_page_boxes,
        # The exact images an agent must open to clear the flags above.
        "verify_images": sorted(str(p) for p in item_verify.glob(f"{item}_*.png")),
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    from mangaeasy.path_safety import portable_prefix_template_arg, relative_subpath_arg
    from mangaeasy.video_pipeline.common import DEFAULT_PROJECT_ROOT, DEFAULT_WORK_DIR

    parser = argparse.ArgumentParser(
        description="Split paged manga into panels with MAGI v3 detection and "
                    "verification sheets (item-pipeline layout)."
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT,
                        help="Project folder containing item subfolders (library/<name>).")
    parser.add_argument("--items", nargs="*", help="Item folders, e.g. 01 02 05-08.")
    parser.add_argument("--item-range", help="Inclusive item range, e.g. 01-19.")
    parser.add_argument("--source-subdir", type=relative_subpath_arg, default="download",
                        help="Subfolder inside each item with the raw pages (default: download).")
    parser.add_argument("--panels-subdir", type=relative_subpath_arg, default="panels",
                        help="Subfolder inside each item to write crops to (default: panels).")
    parser.add_argument("--verify-root", type=Path, default=None,
                        help="Where to write verification sheets "
                             "(default: <work-dir>/page_verify/<project-name>).")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--prefix-template", type=portable_prefix_template_arg, default="{item}_",
                        help="Crop filename prefix; '{item}' expands to the item name.")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--dtype", default="auto", choices=["auto", "fp16", "fp32"])
    parser.add_argument("--reading-direction", default="auto", choices=["auto", "rtl", "ltr"],
                        help="Panel reading order (default: auto = system config; "
                             "rtl for Japanese, ltr for Chinese/Korean).")
    parser.add_argument("--sort", default="numeric", choices=["numeric", "lex"])
    parser.add_argument("--overrides", type=Path, default=None,
                        help="JSON keyed by item -> {page filename: [[x1,y1,x2,y2], ...]} "
                             "that fully replace MAGI's boxes for that page.")
    parser.add_argument("--respect-claims", action="store_true",
                        help="Abort (exit 1) if another live agent's workboard claim covers any "
                             "selected item at this stage (see docs/multi-agent.md).")
    parser.add_argument("--agent", default=None,
                        help="This agent's identity for --respect-claims "
                             "(default: $MANGAEASY_AGENT or user@host).")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    from mangaeasy.video_pipeline.common import item_dirs, merge_item_selection

    args = parse_args(argv)
    if args.respect_claims:
        from mangaeasy.workboard import respect_claims_gate

        if not respect_claims_gate(args.project_root, args.items, args.item_range, ("crop",), args.agent):
            return 1
    project_root = args.project_root.resolve()
    if args.reading_direction == "auto":
        from mangaeasy.panels.direction import project_reading_direction

        args.reading_direction, reason = project_reading_direction(project_root)
        print(f"[page-split] reading direction: {args.reading_direction} ({reason})", flush=True)
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
        else args.work_dir / "page_verify" / project_root.name
    ).resolve()
    verify_dir.mkdir(parents=True, exist_ok=True)

    reports = []
    for i, item_dir in enumerate(selected, 1):
        print(f"MANGAEASY_PROGRESS {i}/{len(selected)}", flush=True)
        reports.append(process_item(item_dir, args, overrides, verify_dir))

    failed = [r["item"] for r in reports if r["status"] == "error"]
    emit_result(
        command="page-split",
        project=project_root.name,
        verify_dir=verify_dir,
        items=reports,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
