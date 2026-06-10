"""mangaeasy.video — shared video utilities."""

import shutil
import subprocess


def get_ffmpeg() -> tuple:
    """Return (ffmpeg_path, ffprobe_path). Raises RuntimeError if either is not on PATH."""
    ffmpeg  = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg and ffprobe must be on PATH. Install ffmpeg and try again.")
    return ffmpeg, ffprobe


def nvenc_available(ffmpeg_path: str | None = None) -> bool:
    """Return True if the available ffmpeg binary supports h264_nvenc."""
    ffmpeg = ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
    try:
        out = subprocess.check_output(
            [ffmpeg, "-hide_banner", "-encoders"],
            text=True, stderr=subprocess.STDOUT,
        )
        return "h264_nvenc" in out
    except Exception:
        return False
