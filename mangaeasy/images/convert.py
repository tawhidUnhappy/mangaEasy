#!/usr/bin/env python3
"""mangaeasy.images.convert — convert non-JPG images in download/ to JPG via ffmpeg."""

import subprocess
import sys

from mangaeasy.config import load_download_config
from mangaeasy.paths import download_dir


def main() -> None:
    load_download_config()  # validates config presence; result unused here
    dl_dir = download_dir()

    if not dl_dir.exists():
        print(f"[ERROR] Download directory not found: {dl_dir}")
        sys.exit(1)

    converted = 0
    for file_path in dl_dir.iterdir():
        if not file_path.is_file() or file_path.suffix.lower() == ".jpg":
            continue
        output_file = file_path.with_suffix(".jpg")
        print(f"Converting: {file_path.name} -> {output_file.name}")
        cmd = ["ffmpeg", "-y", "-hwaccel", "cuda", "-i", str(file_path), str(output_file)]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode == 0:
            file_path.unlink()
            converted += 1
        else:
            # Try without hw-accel on failure
            cmd_cpu = ["ffmpeg", "-y", "-i", str(file_path), str(output_file)]
            result2 = subprocess.run(cmd_cpu, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result2.returncode == 0:
                file_path.unlink()
                converted += 1
            else:
                print(f"[ERROR] Failed to convert: {file_path.name}")
                print(result2.stderr.decode())

    print(f"[DONE] Converted {converted} file(s).")


if __name__ == "__main__":
    main()
