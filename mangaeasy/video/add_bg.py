#!/usr/bin/env python3
"""mangaeasy.video.add_bg — mix background music into a chapter video.

Reads BGM settings from config.system.json → bgm section.
Reads encoder settings from config.system.json → video.encoder.bgm_bitrate
"""

import subprocess
import sys
from pathlib import Path

from mangaeasy.config import PROJECT_ROOT, load_download_config, load_system_config
from mangaeasy.paths import chapter_dir as _chapter_dir, output_video
from mangaeasy.video import get_ffmpeg, nvenc_available


def has_audio_stream(video_path: Path, ffprobe_path: str) -> bool:
    cmd = [ffprobe_path, "-v", "error", "-select_streams", "a",
           "-show_entries", "stream=index", "-of", "json", str(video_path)]
    p = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if p.returncode != 0:
        return False
    import json
    try:
        return len(json.loads(p.stdout or "{}").get("streams", [])) > 0
    except (json.JSONDecodeError, KeyError):
        return False


def process_video(
    video_path: Path, out_path: Path, music_file: Path,
    bg_vol_db: float, audio_bitrate: str, sample_rate: int, use_nvenc: bool,
    ffmpeg_path: str, ffprobe_path: str,
    duck: bool = False, duck_ratio: float = 10.0, duck_attack: float = 5.0, duck_release: float = 500.0,
) -> None:
    print(f"  -> Processing {video_path.name} ... ", end="", flush=True)
    encoder   = ["-c:v", "h264_nvenc"] if use_nvenc else ["-c:v", "libx264", "-preset", "fast"]
    audio_enc = ["-c:a", "aac", "-b:a", audio_bitrate, "-ar", str(sample_rate)]

    if has_audio_stream(video_path, ffprobe_path):
        if duck:
            filter_complex = (
                f"[1:a]volume={bg_vol_db}dB[music];"
                "[0:a]asplit=2[narr_main][narr_sc];"
                f"[music][narr_sc]sidechaincompress=threshold=0.01:ratio={duck_ratio}:attack={duck_attack}:release={duck_release}[music_ducked];"
                "[narr_main][music_ducked]amix=inputs=2:duration=first:dropout_transition=3[aout]"
            )
        else:
            filter_complex = (
                f"[1:a]volume={bg_vol_db}dB[a1];"
                f"[0:a][a1]amix=inputs=2:duration=first:dropout_transition=3[aout]"
            )
        cmd = [ffmpeg_path, "-y", "-i", str(video_path),
               "-stream_loop", "-1", "-i", str(music_file),
               "-filter_complex", filter_complex,
               "-map", "0:v", "-map", "[aout]",
               *encoder, *audio_enc, "-shortest", str(out_path)]
    else:
        cmd = [ffmpeg_path, "-y", "-i", str(video_path),
               "-stream_loop", "-1", "-i", str(music_file),
               "-filter_complex", f"[1:a]volume={bg_vol_db}dB[aout]",
               "-map", "0:v", "-map", "[aout]",
               *encoder, *audio_enc, "-shortest", str(out_path)]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print("FAILED")
        print(result.stderr)
        raise RuntimeError("ffmpeg command failed")
    print("OK")


def main() -> None:
    try:
        FFMPEG, FFPROBE = get_ffmpeg()
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    dl     = load_download_config()
    syscfg = load_system_config()

    chapter = int(dl["chapter"])
    name    = str(dl["name"])

    bgm_cfg       = syscfg.get("bgm", {})
    bg_vol        = float(bgm_cfg.get("volume_db", -22))
    music         = PROJECT_ROOT / bgm_cfg.get("file", "music/Thapin_by_the_sea.wav")
    duck          = bool(bgm_cfg.get("duck", False))
    duck_ratio    = float(bgm_cfg.get("duck_ratio", 10.0))
    duck_attack   = float(bgm_cfg.get("duck_attack", 5.0))
    duck_release  = float(bgm_cfg.get("duck_release", 500.0))

    if not music.exists():
        print(f"[ERROR] Background music not found: {music}")
        sys.exit(1)

    enc_cfg       = syscfg.get("video", {}).get("encoder", {})
    audio_bitrate = str(enc_cfg.get("bgm_bitrate", "192k"))
    sample_rate   = int(syscfg.get("audio", {}).get("sample_rate", 48000))

    video_in  = output_video(name, chapter)
    video_out = _chapter_dir(name, chapter) / f"{chapter:02d}_{name}_with_bgm.mp4"

    if not video_in.is_file():
        print(f"[ERROR] Input video not found: {video_in}")
        print("  Run `mangaeasy render-video` first.")
        sys.exit(1)

    video_out.parent.mkdir(parents=True, exist_ok=True)
    use_nvenc = nvenc_available(FFMPEG)
    if use_nvenc:
        print("[INFO] NVENC detected — using GPU encoder.")
    else:
        print("[INFO] NVENC not available — using libx264.")

    print(f"[INFO] BGM: {music.name}  volume: {bg_vol} dB  duck: {duck}")
    process_video(video_in, video_out, music, bg_vol, audio_bitrate, sample_rate,
                  use_nvenc, FFMPEG, FFPROBE,
                  duck=duck, duck_ratio=duck_ratio, duck_attack=duck_attack, duck_release=duck_release)
    print(f"[DONE] {video_out}")


if __name__ == "__main__":
    main()
