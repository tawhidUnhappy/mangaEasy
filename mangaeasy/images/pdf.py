#!/usr/bin/env python3
"""mangaeasy.images.pdf — convert panel images to PDF.

Two modes:
  lossless=False (default) — re-encodes via Pillow (lossy JPEG inside PDF)
  lossless=True            — packs raw bytes via img2pdf (no re-encoding)
"""

from pathlib import Path

import img2pdf
from PIL import Image

from mangaeasy.config import load_download_config
from mangaeasy.paths import chapter_dir
from mangaeasy.utils import numeric_sort_key

_LOSSY_EXTS    = {".png", ".jpg", ".jpeg"}
_LOSSLESS_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def images_to_pdf(panels_folder: Path, pdf_path: Path, lossless: bool = False) -> None:
    if not panels_folder.exists():
        print(f"[ERROR] panels folder not found: {panels_folder}")
        return

    exts = _LOSSLESS_EXTS if lossless else _LOSSY_EXTS
    images_list = sorted(
        [p for p in panels_folder.iterdir() if p.suffix.lower() in exts],
        key=numeric_sort_key,
    )
    if not images_list:
        print(f"[ERROR] No images found in {panels_folder}")
        return

    if lossless:
        try:
            pdf_bytes = img2pdf.convert([str(p) for p in images_list])
            pdf_path.write_bytes(pdf_bytes)
            print(f"[OK] Lossless PDF written: {pdf_path}  ({len(images_list)} pages)")
        except Exception as exc:
            print(f"[ERROR] Failed to create PDF: {exc}")
    else:
        pil_images = []
        try:
            for img_path in images_list:
                pil_images.append(Image.open(img_path).convert("RGB"))
            pil_images[0].save(pdf_path, save_all=True, append_images=pil_images[1:])
            print(f"[OK] PDF written: {pdf_path}")
        except Exception as exc:
            print(f"[ERROR] Failed to create PDF: {exc}")
        finally:
            for img in pil_images:
                try:
                    img.close()
                except Exception:
                    pass


def main() -> None:
    dl      = load_download_config()
    name    = dl["name"]
    chapter = int(dl["chapter"])
    ch_dir  = chapter_dir(name, chapter)
    images_to_pdf(ch_dir / "panels_filename", ch_dir / f"chapter_{chapter:02d}.pdf")


if __name__ == "__main__":
    main()
