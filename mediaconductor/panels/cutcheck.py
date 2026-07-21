"""mediaconductor.panels.cutcheck — full-resolution crop-QA windows ("virtual windows").

``mediaconductor webtoon-cutcheck`` renders, for every forced auto-split cut and
every short panel recorded in an item's ranges manifest (written by
``webtoon-split``), a full-resolution window of the stitched source strip
around that location, with the cut / panel boundaries drawn on top. Windows
are montaged into fixed-column review sheets that an agent Reads one by one.

This is the QA pass that catches half panels, fused stuck-together panels and
sliced speech bubbles *before* narration/audio are built on top of bad crops —
judge every flagged location on the actual art, never on downscaled contact
sheets. Production verdict guide:

- FIX (add a ``merge`` override; see the manifest's ``merge_note``): the cut
  passes through a figure or a speech bubble, or a short panel is a bubble /
  SFX fragment whose art continues into a neighbour.
- ACCEPT: cuts through background or effect art, bordered thin scenery
  panels, scanlator promo banners (skip those in narration instead).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from mediaconductor.brand import CLI_NAME
from mediaconductor.utils import emit_result

RED = (255, 0, 0)
GREEN = (0, 200, 0)
ORANGE = (255, 140, 0)

_FONT_CANDIDATES = ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"]


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _numeric_key(path: Path) -> list:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", path.stem)]


def stitch_pages(source_dir: Path) -> Image.Image:
    """Stitch an item's raw pages into one strip, same geometry as webtoon-split."""
    pages = sorted(
        (p for p in source_dir.iterdir()
         if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}),
        key=_numeric_key,
    )
    if not pages:
        raise FileNotFoundError(f"no source pages under {source_dir}")
    imgs = [Image.open(p).convert("RGB") for p in pages]
    width = max(im.width for im in imgs)
    heights = [round(im.height * width / im.width) for im in imgs]
    strip = Image.new("RGB", (width, sum(heights)), "white")
    y = 0
    for im, h in zip(imgs, heights, strict=True):
        strip.paste(im.resize((width, h)) if im.size != (width, h) else im, (0, y))
        y += h
    return strip


def parse_forced_cuts(manifest: dict) -> list[int]:
    cuts = []
    for token in manifest.get("forced_cuts", []):
        m = re.match(r"y=(\d+)", str(token))
        if m:
            cuts.append(int(m.group(1)))

    # ``forced_cuts`` records the splitter's original auto-cut candidates so
    # agents can resolve overrides against stable stitched-strip coordinates.
    # Once overrides merge one of those boundaries away, however, it is no
    # longer a cut in the generated panels and must not be presented to crop
    # QA again.  Otherwise the documented split -> QA -> override loop can
    # never reach a clean exit: the model keeps reviewing a red line that no
    # longer exists in ``final``.
    if manifest.get("overrides_applied") and isinstance(manifest.get("final"), list):
        final_tops = {
            int(panel["top"])
            for panel in manifest["final"]
            if isinstance(panel, dict) and "top" in panel
        }
        final_bottoms = {
            int(panel["bottom"])
            for panel in manifest["final"]
            if isinstance(panel, dict) and "bottom" in panel
        }
        live_boundaries = final_tops & final_bottoms
        cuts = [y for y in cuts if y in live_boundaries]
    return cuts


def window_bounds(y_top: int, y_bottom: int, strip_height: int, margin: int) -> tuple[int, int]:
    return max(0, y_top - margin), min(strip_height, y_bottom + margin)


def render_window(strip: Image.Image, top: int, bottom: int, thumb_width: int,
                  marks: list[tuple[int, tuple, str]]) -> Image.Image:
    win = strip.crop((0, top, strip.width, bottom)).copy()
    draw = ImageDraw.Draw(win)
    font = _load_font(max(18, strip.width // 40))
    for y, color, label in marks:
        ly = y - top
        draw.line([(0, ly), (win.width, ly)], fill=color, width=5)
        draw.text((10, min(max(0, ly + 6), win.height - 30)), label, fill=color, font=font)
    if win.width > thumb_width:
        win = win.resize((thumb_width, round(win.height * thumb_width / win.width)))
    return win


def montage(windows: list[tuple[str, Image.Image]], columns: int, pad: int = 14,
            header_h: int = 40) -> Image.Image:
    cols = windows[:columns]
    cell_w = max(im.width for _, im in cols)
    cell_h = max(im.height for _, im in cols) + header_h
    sheet = Image.new("RGB", (columns * (cell_w + pad) + pad, cell_h + 2 * pad), "black")
    draw = ImageDraw.Draw(sheet)
    font = _load_font(28)
    for i, (name, im) in enumerate(cols):
        x = pad + i * (cell_w + pad)
        draw.text((x + 4, pad), name, fill=(255, 230, 0), font=font)
        sheet.paste(im, (x, pad + header_h))
    return sheet


def parse_args() -> argparse.Namespace:
    from mediaconductor.path_safety import relative_subpath_arg
    from mediaconductor.video_pipeline.common import DEFAULT_PROJECT_ROOT, DEFAULT_WORK_DIR

    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} webtoon-cutcheck",
        description="Render full-resolution review windows around every forced cut and "
                    "short panel from a webtoon-split ranges manifest.",
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--items", nargs="*", help="Item folders, e.g. 01 02 05-08.")
    parser.add_argument("--item-range", help="Inclusive item range, e.g. 01-07.")
    parser.add_argument("--source-subdir", type=relative_subpath_arg, default="download")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--verify-root", type=Path, default=None,
                        help="Where webtoon-split wrote <item>_ranges.json "
                             "(default: <work-dir>/webtoon_verify/<project-name>).")
    parser.add_argument("--output-root", type=Path, default=None,
                        help="Where to write windows and sheets "
                             "(default: <work-dir>/cutcheck/<project-name>).")
    parser.add_argument("--window", type=int, default=650,
                        help="Rows of context above/below each flagged location (default 650).")
    parser.add_argument("--short-height", type=int, default=460,
                        help="Panels shorter than this get a review window (default 460).")
    parser.add_argument("--thumb-width", type=int, default=650,
                        help="Width each window is scaled to inside sheets (default 650).")
    parser.add_argument("--columns", type=int, default=3, help="Windows per sheet (default 3).")
    return parser.parse_args()


def main() -> int:
    from mediaconductor.video_pipeline.common import item_dirs, merge_item_selection

    args = parse_args()
    project_root = args.project_root.resolve()
    selected = item_dirs(project_root, merge_item_selection(args.items, args.item_range))
    if not selected:
        print(f"[FATAL] No item folders found under {project_root}")
        return 1

    verify_dir = (args.verify_root or args.work_dir / "webtoon_verify" / project_root.name).resolve()
    out_dir = (args.output_root or args.work_dir / "cutcheck" / project_root.name).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    windows: list[tuple[str, Image.Image]] = []
    per_item: dict[str, dict] = {}
    for i, item_dir in enumerate(selected, 1):
        print(f"MEDIACONDUCTOR_PROGRESS {i}/{len(selected)}", flush=True)
        item = item_dir.name
        manifest_path = verify_dir / f"{item}_ranges.json"
        if not manifest_path.is_file():
            print(f"[{item}] no ranges manifest at {manifest_path} — run webtoon-split first")
            per_item[item] = {"error": "missing manifest"}
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        strip = stitch_pages(item_dir / args.source_subdir)
        cuts = parse_forced_cuts(manifest)
        shorts = [p for p in manifest.get("final", [])
                  if p.get("height", 0) < args.short_height]
        for y in cuts:
            top, bottom = window_bounds(y, y, strip.height, args.window)
            name = f"{item}_cut_y{y}"
            win = render_window(strip, top, bottom, args.thumb_width,
                                [(y, RED, f"CUT y={y}")])
            win.save(out_dir / f"{name}.jpg", quality=88)
            windows.append((name, win))
        for panel in shorts:
            top, bottom = window_bounds(panel["top"], panel["bottom"], strip.height, args.window)
            name = f"{item}_short_p{panel['index']:03d}"
            win = render_window(strip, top, bottom, args.thumb_width, [
                (panel["top"], GREEN, f"#{panel['index']} top y={panel['top']}"),
                (panel["bottom"], ORANGE, f"#{panel['index']} bottom y={panel['bottom']}"),
            ])
            win.save(out_dir / f"{name}.jpg", quality=88)
            windows.append((name, win))
        per_item[item] = {"forced_cuts": len(cuts), "short_panels": len(shorts)}
        print(f"[{item}] windows: {len(cuts)} cut(s), {len(shorts)} short panel(s)", flush=True)

    sheets = []
    for n in range(0, len(windows), args.columns):
        sheet = montage(windows[n:n + args.columns], args.columns)
        sheet_path = out_dir / f"sheet_{n // args.columns + 1:02d}.jpg"
        sheet.save(sheet_path, quality=88)
        sheets.append(str(sheet_path))
    print(f"{len(windows)} window(s) -> {len(sheets)} sheet(s) under {out_dir}")
    print("Read every sheet at full size; judge each flagged location on the art "
          "(FIX = figure/bubble cut; ACCEPT = background/banner/bordered thin panel).")
    emit_result(command="webtoon-cutcheck", output_dir=out_dir, windows=len(windows),
                sheets=sheets, items=per_item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
