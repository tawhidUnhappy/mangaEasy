#!/usr/bin/env python3
"""mangaeasy.images.watermark — add filename watermarks to panel images."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from mangaeasy.config import load_download_config
from mangaeasy.paths import panels_dir


def add_watermark(input_folder: Path) -> None:
    if not input_folder.exists() or not input_folder.is_dir():
        print(f"[ERROR] Folder not found: {input_folder}")
        return

    output_folder = input_folder.parent / f"{input_folder.name}_filename"
    output_folder.mkdir(exist_ok=True)

    try:
        font = ImageFont.truetype("arial.ttf", 30)
    except Exception:
        font = ImageFont.load_default()

    for img_file in input_folder.iterdir():
        if img_file.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".gif"}:
            continue
        with Image.open(img_file).convert("RGB") as im:
            draw = ImageDraw.Draw(im)
            text = img_file.name
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            padding = 10
            new_h = im.height + text_h + 2 * padding
            new_im = Image.new("RGB", (im.width, new_h), color=(0, 0, 0))
            new_im.paste(im, (0, 0))
            draw = ImageDraw.Draw(new_im)
            draw.text((padding, im.height + padding), text, font=font, fill=(255, 255, 255))
            new_im.save(output_folder / img_file.name)
            print(f"[OK] {img_file.name}")


def main() -> None:
    add_watermark(panels_dir())


if __name__ == "__main__":
    main()
