"""mangaeasy.images.thumbnail_compose — add text furniture to a thumbnail base.

``mangaeasy thumbnail-compose`` turns a generated key-art image (usually the
best ``mangaeasy zimage`` variant) into a finished YouTube thumbnail:
1280×720 canvas (cover-scaled, center-cropped), 1–3 short text blocks in a
bold impact-style font with the channel's proven treatment (black stroke
≈ 12 % of the font size, #FFE600/white fills), an optional arrow, and a thin
white inset border.

Deterministic on purpose: an agent writes a tiny spec, renders, opens the
output to inspect it at full size, adjusts the spec, and re-renders. Two ways
to drive it:

- quick: repeated ``--text "3-5 WORDS"`` flags — stacked top-left,
  alternating yellow/white fills;
- full: ``--spec spec.json`` — ``{"blocks": [{"text", "x", "y", "size",
  "fill", "stroke"}...], "arrow": {"from": [x,y], "to": [x,y]}, "border": true}``.

The previous output file is archived (old/run_NNNN/), never clobbered.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from mangaeasy.utils import archive_before_overwrite, emit_result

DEFAULT_SIZE = (1280, 720)
DEFAULT_FONT_SIZE = 104          # inside the playbook's 90-120 pt band
STROKE_FRACTION = 0.12           # black stroke ≈ 12 % of font size
FILL_CYCLE = ("#FFE600", "#FFFFFF")
MARGIN = 44

# Impact first (the channel look), then common bold fallbacks per platform.
_FONT_CANDIDATES = [
    "impact.ttf", "Impact.ttf",
    "arialbd.ttf", "Arial Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _load_font(size: int, font_path: str | None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [font_path] if font_path else _FONT_CANDIDATES
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    print("[warn] no TrueType font found — using PIL's small default; pass --font", flush=True)
    return ImageFont.load_default()


def cover_canvas(base: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale *base* to cover *size*, center-crop the overflow."""
    tw, th = size
    scale = max(tw / base.width, th / base.height)
    resized = base.resize((round(base.width * scale), round(base.height * scale)), Image.LANCZOS)
    left = (resized.width - tw) // 2
    top = (resized.height - th) // 2
    return resized.crop((left, top, left + tw, top + th)).convert("RGB")


def draw_block(draw: ImageDraw.ImageDraw, block: dict, font_path: str | None) -> None:
    size = int(block.get("size", DEFAULT_FONT_SIZE))
    font = _load_font(size, font_path)
    draw.text(
        (int(block["x"]), int(block["y"])),
        str(block["text"]),
        font=font,
        fill=block.get("fill", FILL_CYCLE[0]),
        stroke_width=max(2, round(size * STROKE_FRACTION)),
        stroke_fill=block.get("stroke", "#000000"),
    )


def draw_arrow(draw: ImageDraw.ImageDraw, arrow: dict) -> None:
    (x1, y1), (x2, y2) = arrow["from"], arrow["to"]
    color = arrow.get("color", "#FF3333")
    width = int(arrow.get("width", 14))
    draw.line([(x1, y1), (x2, y2)], fill=color, width=width)
    # Arrowhead: two short strokes back from the tip.
    import math
    angle = math.atan2(y2 - y1, x2 - x1)
    head = width * 3.2
    for offset in (math.radians(150), math.radians(-150)):
        draw.line(
            [(x2, y2),
             (x2 + head * math.cos(angle + offset), y2 + head * math.sin(angle + offset))],
            fill=color, width=width,
        )


def draw_border(draw: ImageDraw.ImageDraw, size: tuple[int, int]) -> None:
    w, h = size
    inset, thickness = 14, 6
    draw.rectangle([inset, inset, w - inset - 1, h - inset - 1],
                   outline="#FFFFFF", width=thickness)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="mangaeasy thumbnail-compose",
        description="Compose a YouTube thumbnail: base art + bold stroked text "
                    "blocks + optional arrow + white inset border (1280x720).",
    )
    parser.add_argument("--base", type=Path, required=True,
                        help="Base image (e.g. the best zimage variant).")
    parser.add_argument("--output", type=Path, required=True, help="Output PNG/JPG path.")
    parser.add_argument("--text", action="append", default=[], metavar="WORDS",
                        help="Quick mode: one text block (repeatable, stacked top-left, "
                             "alternating yellow/white). Keep each to 3-5 punchy words.")
    parser.add_argument("--spec", type=Path, default=None,
                        help="Full mode: JSON spec with blocks/arrow/border (see module help).")
    parser.add_argument("--width", type=int, default=DEFAULT_SIZE[0])
    parser.add_argument("--height", type=int, default=DEFAULT_SIZE[1])
    parser.add_argument("--font", default=None, help="Path to a .ttf to use for all blocks.")
    parser.add_argument("--no-border", action="store_true",
                        help="Skip the thin white inset border.")
    args = parser.parse_args()

    if not args.base.is_file():
        print(f"ERROR: base image not found: {args.base}", file=sys.stderr)
        return 1
    if not args.text and not args.spec:
        print("ERROR: provide --text (repeatable) or --spec", file=sys.stderr)
        return 2

    spec: dict = {}
    if args.spec is not None:
        try:
            spec = json.loads(args.spec.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            print(f"ERROR: invalid spec JSON: {exc}", file=sys.stderr)
            return 2

    size = (args.width, args.height)
    canvas = cover_canvas(Image.open(args.base), size)
    draw = ImageDraw.Draw(canvas)

    blocks = list(spec.get("blocks", []))
    y = MARGIN
    for i, text in enumerate(args.text):
        blocks.append({"text": text, "x": MARGIN, "y": y,
                       "size": DEFAULT_FONT_SIZE, "fill": FILL_CYCLE[i % len(FILL_CYCLE)]})
        y += round(DEFAULT_FONT_SIZE * 1.28)

    for block in blocks:
        draw_block(draw, block, args.font)
    if spec.get("arrow"):
        draw_arrow(draw, spec["arrow"])
    if spec.get("border", True) and not args.no_border:
        draw_border(draw, size)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    archived = archive_before_overwrite(args.output)
    if archived:
        print(f"[info] previous thumbnail archived: {archived}")
    canvas.save(args.output)
    print(f"[info] thumbnail written: {args.output} ({size[0]}x{size[1]}, "
          f"{len(blocks)} text block(s))")
    print("[info] inspect it at full size before upload (faces, text overlap, edges).")
    emit_result(outputs=[args.output], blocks=len(blocks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
