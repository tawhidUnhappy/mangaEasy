"""mangaeasy.images.watermark_util
Shared script-font watermark helpers used by panels/process.py and video/render.py.
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

_SCRIPT_FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoesc.ttf",
    "C:/Windows/Fonts/segoescb.ttf",
    "C:/Windows/Fonts/BRUSHSCI.TTF",
    "C:/Windows/Fonts/segoepr.ttf",
    "C:/Windows/Fonts/times.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def load_watermark_font(size: int) -> ImageFont.FreeTypeFont:
    for path in _SCRIPT_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def apply_watermark(img: Image.Image, cfg: dict) -> Image.Image:
    """Render a script-font watermark at the bottom-right corner of img."""
    text    = cfg.get("text",      "@YourChannel")
    opacity = float(cfg.get("opacity",  0.55))
    size    = int(cfg.get("font_size",  48))
    padding = int(cfg.get("padding",    20))

    if not text:
        return img

    font = load_watermark_font(size)

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bbox  = probe.textbbox((0, 0), text, font=font)
    tw    = bbox[2] - bbox[0]
    th    = bbox[3] - bbox[1]
    ox, oy = -bbox[0], -bbox[1]

    spread = 3
    x = img.width  - tw - padding - ox - spread
    y = img.height - th - padding - oy - spread

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw  = ImageDraw.Draw(layer)

    halo_rgb   = (55, 25, 8)
    halo_alpha = int(210 * opacity)
    for dx in range(-spread, spread + 1):
        for dy in range(-spread, spread + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + ox + dx, y + oy + dy), text, font=font,
                      fill=(*halo_rgb, halo_alpha))

    draw.text((x + ox, y + oy), text, font=font,
              fill=(255, 255, 255, int(240 * opacity)))

    base   = img.convert("RGBA")
    merged = Image.alpha_composite(base, layer)
    return merged.convert("RGB")
