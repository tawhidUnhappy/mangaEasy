"""mangaeasy.video_pipeline.narration_sheets — panel+text pairs for semantic QA.

``mangaeasy narration-review-sheets`` renders review sheets that pair every
narration entry's panel image with (a) the narration text that will be spoken
over it and (b) the panel's OCR'd bubble text when ``panel-transcript`` has
been run. This is the *semantic* half of narration verification that
``narration-check`` deliberately does not do — an agent Reads each sheet and
checks, per panel:

1. the narration describes THIS panel (not a summary smeared across several);
2. quoted/paraphrased dialogue is attributed to the right character — compare
   against the OCR column, which is what the bubbles actually say;
3. paraphrases stay faithful to the OCR text (loose retelling reads wrong);
4. the line reads naturally when spoken aloud.

Fix problems by editing narration.json, delete the affected WAVs
(``video-audio-audit --fix`` after emptying them, or remove by stem), and
re-run audio generation — it only regenerates missing files.
"""

from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from mangaeasy.brand import CLI_NAME
from mangaeasy.utils import emit_result

PANEL_W = 560
PANEL_MAX_H = 900
TEXT_W = 620
PAD = 16

_FONT_CANDIDATES = ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"]
_BODY_CANDIDATES = ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf"]


def _font(size: int, candidates: list[str]) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def load_transcript(item_dir: Path) -> dict[str, str]:
    path = item_dir / "transcript.json"
    if not path.is_file():
        return {}
    try:
        entries = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return {e.get("image", ""): (e.get("ocr") or "") for e in entries if isinstance(e, dict)}


def wrap(text: str, width: int = 46) -> list[str]:
    lines: list[str] = []
    for para in (text or "").splitlines() or [""]:
        lines.extend(textwrap.wrap(para, width=width) or [""])
    return lines


def render_entry(item_dir: Path, entry: dict, ocr: str) -> Image.Image:
    head_font = _font(26, _FONT_CANDIDATES)
    body_font = _font(24, _BODY_CANDIDATES)
    image_path = item_dir / "panels" / entry["image"]
    if image_path.is_file():
        panel = Image.open(image_path).convert("RGB")
        panel.thumbnail((PANEL_W, PANEL_MAX_H))
        cropped_note = ""
    else:
        panel = Image.new("RGB", (PANEL_W, 200), (60, 0, 0))
        cropped_note = "IMAGE MISSING"

    narration_lines = wrap(entry.get("narration", ""))
    ocr_lines = wrap(ocr, width=52) if ocr else ["(no transcript — run panel-transcript)"]
    line_h = 30
    text_h = (len(narration_lines) + len(ocr_lines) + 4) * line_h + 2 * PAD
    cell_h = max(panel.height + 2 * PAD, text_h, 240)
    cell = Image.new("RGB", (PANEL_W + TEXT_W + 3 * PAD, cell_h + 40), (18, 18, 18))
    draw = ImageDraw.Draw(cell)
    draw.text((PAD, 8), f"{entry['image']}  {cropped_note}", fill=(255, 230, 0), font=head_font)
    cell.paste(panel, (PAD, 40 + PAD))
    x = PANEL_W + 2 * PAD
    y = 40 + PAD
    draw.text((x, y), "NARRATION:", fill=(120, 220, 120), font=head_font)
    y += line_h
    for line in narration_lines:
        draw.text((x, y), line, fill=(235, 235, 235), font=body_font)
        y += line_h
    y += line_h // 2
    draw.text((x, y), "BUBBLES (OCR):", fill=(120, 170, 255), font=head_font)
    y += line_h
    for line in ocr_lines:
        draw.text((x, y), line, fill=(170, 170, 170), font=body_font)
        y += line_h
    return cell


def parse_args() -> argparse.Namespace:
    from mangaeasy.video_pipeline.common import DEFAULT_PROJECT_ROOT, DEFAULT_WORK_DIR

    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} narration-review-sheets",
        description="Render panel + narration + OCR review sheets for semantic and "
                    "speaker verification of narration.json.",
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--items", nargs="*")
    parser.add_argument("--item-range")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--output-root", type=Path, default=None,
                        help="Default: <work-dir>/narration_review/<project-name>.")
    parser.add_argument("--per-sheet", type=int, default=4,
                        help="Entries per sheet (default 4).")
    parser.add_argument("--only-images", nargs="*", default=None,
                        help="Limit to these image names/stems (e.g. the panels-remap "
                             "review list, or panels flagged in an earlier pass).")
    return parser.parse_args()


def main() -> int:
    from mangaeasy.video_pipeline.common import item_dirs, merge_item_selection
    from mangaeasy.video_pipeline.item_assets import load_narration

    args = parse_args()
    project_root = args.project_root.resolve()
    selected = item_dirs(project_root, merge_item_selection(args.items, args.item_range))
    if not selected:
        print(f"[FATAL] No item folders found under {project_root}")
        return 1
    out_root = (args.output_root or args.work_dir / "narration_review" / project_root.name).resolve()

    only = None
    if args.only_images:
        only = {Path(name).stem for name in args.only_images}

    all_sheets: list[str] = []
    per_item: dict[str, dict] = {}
    for i, item_dir in enumerate(selected, 1):
        print(f"MANGAEASY_PROGRESS {i}/{len(selected)}", flush=True)
        entries = load_narration(item_dir)
        if only is not None:
            entries = [e for e in entries if Path(e["image"]).stem in only]
        transcript = load_transcript(item_dir)
        missing_images = [e["image"] for e in entries
                          if not (item_dir / "panels" / e["image"]).is_file()]
        out_dir = out_root / item_dir.name
        out_dir.mkdir(parents=True, exist_ok=True)
        sheets = []
        for n in range(0, len(entries), args.per_sheet):
            cells = [render_entry(item_dir, e, transcript.get(e["image"], ""))
                     for e in entries[n:n + args.per_sheet]]
            width = max(c.width for c in cells)
            sheet = Image.new("RGB", (width, sum(c.height + PAD for c in cells)), (0, 0, 0))
            y = 0
            for c in cells:
                sheet.paste(c, (0, y))
                y += c.height + PAD
            path = out_dir / f"review_{n // args.per_sheet + 1:03d}.jpg"
            sheet.save(path, quality=86)
            sheets.append(str(path))
        all_sheets.extend(sheets)
        per_item[item_dir.name] = {
            "entries": len(entries), "sheets": len(sheets),
            "with_ocr": sum(1 for e in entries if transcript.get(e["image"])),
            "missing_images": missing_images,
        }
        print(f"[{item_dir.name}] {len(entries)} entries -> {len(sheets)} sheet(s)"
              + (f", MISSING IMAGES: {missing_images}" if missing_images else ""), flush=True)

    print(f"{len(all_sheets)} sheet(s) under {out_root}")
    print("Read every sheet: narration must describe THAT panel, dialogue must match the "
          "OCR column and be attributed to the right character.")
    emit_result(command="narration-review-sheets", output_dir=out_root,
                sheets=len(all_sheets), items=per_item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
