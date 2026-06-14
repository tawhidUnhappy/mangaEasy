"""mangaeasy.images.ai_zip — labelled ZIP of chapter panels for AI context.

Stamps each panel with a filename banner ABOVE the image (never overlapping
content) and packs all watermarked copies into a ZIP at original resolution.
Original panel files are never modified.
"""

from __future__ import annotations
import io
import zipfile
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/calibri.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/cour.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode == "RGB":
        return img
    if img.mode == "P":
        img = img.convert("RGBA")
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img.convert("RGB"), mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def _stamp_label(img: Image.Image, label: str) -> Image.Image:
    """Return a new image with a dark filename banner added ABOVE the panel."""
    img = _to_rgb(img)
    w, h = img.size

    font_size = max(18, min(90, int(w * 0.030)))
    font = _load_font(font_size)

    _d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    bb = _d.textbbox((0, 0), label, font=font)
    text_h = bb[3] - bb[1]

    pad_y = max(6, int(font_size * 0.35))
    pad_x = max(8, int(font_size * 0.50))
    banner_h = text_h + pad_y * 2

    out = Image.new("RGB", (w, banner_h + h), (18, 18, 18))
    draw = ImageDraw.Draw(out)
    draw.rectangle([(0, 0), (w, banner_h)], fill=(28, 30, 36))
    draw.text((pad_x + 1, pad_y + 1), label, font=font, fill=(0, 0, 0))
    draw.text((pad_x, pad_y), label, font=font, fill=(225, 232, 248))
    draw.rectangle([(0, banner_h - 2), (w, banner_h)], fill=(65, 120, 175))
    out.paste(img, (0, banner_h))
    return out


def _encode(img: Image.Image, ext: str) -> tuple[bytes, str]:
    """Encode *img* matching the source format; return (bytes, archive_name_ext)."""
    buf = io.BytesIO()
    if ext in (".jpg", ".jpeg"):
        img.save(buf, format="JPEG", quality=95, subsampling=0)
        return buf.getvalue(), ext
    else:
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), ".png"


def panels_to_ai_zip(
    panels_dir: Path,
    out_path: Path,
    log: Callable[[str], None] = print,
) -> int:
    """Pack watermarked copies of all panels into *out_path* (ZIP).

    Each panel gets a filename banner above it; originals are untouched.
    Returns the number of panels included.
    """
    files = sorted(p for p in panels_dir.iterdir() if p.suffix.lower() in _IMAGE_EXTS)
    if not files:
        raise FileNotFoundError(f"no panel images found in {panels_dir}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    # ZIP_STORED: images are already compressed — deflating gains nothing and wastes CPU.
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for p in files:
            try:
                img = Image.open(p)
                img.load()
            except Exception as exc:
                log(f"[ai-zip] skip {p.name}: {exc}")
                continue

            stamped = _stamp_label(img, p.name)
            img.close()

            data, out_ext = _encode(stamped, p.suffix.lower())
            zf.writestr(p.stem + out_ext, data)
            count += 1
            log(f"[ai-zip] {p.name}")

    if count == 0:
        out_path.unlink(missing_ok=True)
        raise FileNotFoundError("all panel images failed to load")

    log(f"[ai-zip] ✓ {count} panels → {out_path.name}")
    return count
