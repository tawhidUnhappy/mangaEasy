#!/usr/bin/env python3
"""mangaeasy.images.pdf_lossless — shim for the mangaeasy to-pdf-lossless CLI entry point."""

from mangaeasy.config import load_download_config
from mangaeasy.images.pdf import images_to_pdf
from mangaeasy.paths import chapter_dir


def main() -> None:
    dl      = load_download_config()
    name    = dl["name"]
    chapter = int(dl["chapter"])
    ch_dir  = chapter_dir(name, chapter)
    images_to_pdf(ch_dir / "panels_filename", ch_dir / f"chapter_{chapter:02d}_lossless.pdf", lossless=True)


if __name__ == "__main__":
    main()
