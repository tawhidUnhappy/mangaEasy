#!/usr/bin/env python3
"""mangaeasy.video.render — quality-first chapter video renderer.

Pipeline:
  1. Read panel images from panels/
  2. Read audio from {chapter_dir}/audio_faded/ (falls back to audio/ if not found)
  3. Pre-render PNG frames
  4. Normalise each audio clip to PCM
  5. Join all PCM clips → one chapter WAV
  6. 2-pass loudnorm on the chapter WAV
  7. Build video-only slideshow (NVENC or libx264)
  8. Mux video + normalised audio → final MP4
"""

from __future__ import annotations

import fnmatch
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable
import traceback

from PIL import Image, ImageOps

from mangaeasy.config import PROJECT_ROOT, load_download_config, load_system_config
from mangaeasy.images.watermark_util import apply_watermark
from mangaeasy.paths import (
    chapter_dir as _chapter_dir,
    panels_dir,
    processed_panels_dir,
    audio_dir,
    faded_audio_dir,
    tmp_dir,
    output_video,
)
from mangaeasy.video import nvenc_available
from mangaeasy.video.blur_background import (
    BlurBackgroundOptions,
    compose_blurred_panel_pil,
    render_blurred_panel_ffmpeg,
)

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
AUDIO_EXTS = (".wav", ".mp3", ".flac", ".m4a", ".ogg")


def _sys_video() -> dict:
    return load_system_config().get("video", {})

def _sys_audio() -> dict:
    return load_system_config().get("audio", {})

def _sys_enc() -> dict:
    return _sys_video().get("encoder", {})

def _sys_loudnorm() -> dict:
    return _sys_audio().get("loudnorm", {})


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


def ffprobe_duration(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        text=True,
    ).strip()
    return float(out)


def extract_loudnorm_json(stderr_text: str) -> dict:
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(stderr_text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(stderr_text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and ("input_i" in obj or "input_I" in obj):
            return obj
    raise RuntimeError("Failed to parse loudnorm JSON from ffmpeg stderr.")


def find_files(root: Path, patterns: Iterable[str]) -> Iterable[Path]:
    for path, _, files in os.walk(root):
        for pattern in patterns:
            for filename in fnmatch.filter(files, pattern):
                yield Path(path) / filename


def load_paths() -> dict:
    dl     = load_download_config()
    syscfg = load_system_config()

    chapter = int(dl["chapter"])
    name    = str(dl["name"])

    vid = syscfg.get("video", {})
    w   = int(vid.get("width",  1920))
    h   = int(vid.get("height", 1080))

    bg_rel = vid.get("background_image", "background_image/grid_background_1920x1080.png")
    bg_img = PROJECT_ROOT / bg_rel

    ch_dir        = _chapter_dir(name, chapter)
    tmp_root      = tmp_dir(name, chapter)
    processed_dir = processed_panels_dir(name, chapter)
    panels_dir_p  = panels_dir(name, chapter)

    upscale_on = bool(syscfg.get("process_panels", {}).get("upscale", True))
    if upscale_on and processed_dir.exists() and any(processed_dir.iterdir()):
        image_root = processed_dir
        print(f"[INFO] Using processed panels from: {processed_dir.name}/")
    else:
        image_root = panels_dir_p
        reason = "upscale disabled" if not upscale_on else "no processed panels found"
        print(f"[INFO] Using original panels from: {panels_dir_p.name}/  ({reason})")

    return {
        "name":             name,
        "chapter":          chapter,
        "target_res":       (w, h),
        "background_image": bg_img,
        "video_config":     vid,
        "chapter_dir":      ch_dir,
        "image_root":       image_root,
        "raw_audio_root":   audio_dir(name, chapter),
        "faded_audio_root": faded_audio_dir(name, chapter),
        "frames_dir":       tmp_root / "frames",
        "pcm_dir":          tmp_root / "audio_pcm",
        "lists_dir":        tmp_root / "lists",
        "build_dir":        tmp_root / "build",
        "output_path":      output_video(name, chapter),
    }


def collect_pairs(image_root: Path, audio_root: Path) -> list[tuple[Path, Path]]:
    audio_map: dict[str, Path] = {}
    for ext in AUDIO_EXTS:
        for audio in find_files(audio_root, [f"*{ext}"]):
            audio_map.setdefault(audio.stem.lower(), audio)
    pairs: list[tuple[Path, Path]] = []
    for ext in IMAGE_EXTS:
        for image in find_files(image_root, [f"*{ext}"]):
            audio = audio_map.get(image.stem.lower())
            if audio is not None:
                pairs.append((image, audio))
    pairs.sort(key=lambda item: item[0].name)
    return pairs


def load_background(target_res: tuple[int, int], bg_path: Path) -> Image.Image:
    if bg_path.exists():
        with Image.open(bg_path).convert("RGB") as bg:
            return ImageOps.fit(bg, target_res, Image.LANCZOS).copy()
    return Image.new("RGB", target_res, (0, 0, 0))


def background_style(video_cfg: dict | None) -> str:
    style = str((video_cfg or {}).get("background_style") or "blur").lower()
    if style not in {"blur", "image", "black"}:
        print(f"[WARN] Unknown video.background_style={style!r}; using blur")
        return "blur"
    return style


def _tmp_frame_path(out_path: Path) -> Path:
    return out_path.with_name(f"{out_path.stem}.tmp{out_path.suffix}")


def _save_with_watermark(src: Path, dst: Path, watermark_cfg: dict | None) -> None:
    if watermark_cfg and watermark_cfg.get("enabled"):
        with Image.open(src).convert("RGBA") as frame:
            apply_watermark(frame, watermark_cfg).save(dst, "PNG", optimize=True)
    else:
        src.replace(dst)


def render_blurred_frame(
    image_path: Path,
    target_res: tuple[int, int],
    out_path: Path,
    video_cfg: dict | None = None,
    watermark_cfg: dict | None = None,
) -> None:
    width, height = target_res
    options = BlurBackgroundOptions.from_mapping(video_cfg)
    tmp = _tmp_frame_path(out_path)
    try:
        try:
            render_blurred_panel_ffmpeg(image_path, tmp, width, height, options, run, log=print)
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            print(f"[WARN] ffmpeg blur failed for {image_path.name}; using PIL fallback ({exc})")
            compose_blurred_panel_pil(image_path, tmp, width, height, options)
        _save_with_watermark(tmp, out_path, watermark_cfg)
    finally:
        tmp.unlink(missing_ok=True)


def render_frame(image_path: Path, canvas: Image.Image, target_res: tuple[int, int],
                 out_path: Path, watermark_cfg: dict | None = None) -> None:
    with Image.open(image_path).convert("RGBA") as image:
        fitted = ImageOps.contain(image, target_res, Image.LANCZOS)
        frame = canvas.copy()
        x = (target_res[0] - fitted.width) // 2
        y = (target_res[1] - fitted.height) // 2
        frame.paste(fitted, (x, y), fitted)
        if watermark_cfg and watermark_cfg.get("enabled"):
            frame = apply_watermark(frame, watermark_cfg)
        frame.save(out_path, "PNG", optimize=True)


def prerender_frames(pairs: list, frames_dir: Path, target_res: tuple[int, int],
                     bg_path: Path, video_cfg: dict | None = None,
                     watermark_cfg: dict | None = None) -> None:
    frames_dir.mkdir(parents=True, exist_ok=True)
    style = background_style(video_cfg)
    if style == "blur":
        canvas = None
    elif style == "black":
        canvas = Image.new("RGB", target_res, (0, 0, 0))
    else:
        canvas = load_background(target_res, bg_path)
    for idx, (image, _) in enumerate(pairs, start=1):
        out = frames_dir / f"{image.stem}.png"
        if style == "blur":
            render_blurred_frame(image, target_res, out, video_cfg, watermark_cfg)
        else:
            assert canvas is not None
            render_frame(image, canvas, target_res, out, watermark_cfg)
        print(f"[FRAME] {idx}/{len(pairs)} {out.name}")


def normalize_clip_to_pcm(src: Path, dst: Path, sample_rate: int, channels: int) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-i", str(src), "-map", "0:a:0",
         "-ac", str(channels), "-ar", str(sample_rate),
         "-sample_fmt", "s16", "-c:a", "pcm_s16le", str(dst)])


def build_pcm_clips(pairs: list, pcm_dir: Path, sample_rate: int, channels: int) -> list[Path]:
    pcm_paths: list[Path] = []
    for idx, (_, audio) in enumerate(pairs, start=1):
        dst = pcm_dir / f"{idx:05d}_{audio.stem}.wav"
        normalize_clip_to_pcm(audio, dst, sample_rate, channels)
        pcm_paths.append(dst)
        print(f"[PCM]   {idx}/{len(pairs)} {dst.name}")
    return pcm_paths


def write_concat_audio_list(pcm_paths: list[Path], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for p in pcm_paths:
            f.write(f"file '{p.resolve().as_posix()}'\n")


def join_pcm_audio(pcm_paths: list[Path], out_path: Path, concat_list: Path) -> None:
    write_concat_audio_list(pcm_paths, concat_list)
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(concat_list), "-c:a", "pcm_s16le", str(out_path)])


def loudnorm_wav(input_wav: Path, output_wav: Path,
                 lufs: float, tp: float, lra: float,
                 sample_rate: int, channels: int) -> None:
    print("[INFO] loudnorm pass 1 (measure)")
    pass1 = run(["ffmpeg", "-y", "-i", str(input_wav), "-vn",
                 "-af", f"loudnorm=I={lufs}:TP={tp}:LRA={lra}:print_format=json",
                 "-f", "null", "-"])
    stats = extract_loudnorm_json(pass1.stderr)
    ln_filter = (
        f"loudnorm=I={lufs}:TP={tp}:LRA={lra}:"
        f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
        f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}:linear=true"
    )
    print("[INFO] loudnorm pass 2 (apply)")
    run(["ffmpeg", "-y", "-i", str(input_wav), "-af", ln_filter,
         "-ac", str(channels), "-ar", str(sample_rate),
         "-sample_fmt", "s16", "-c:a", "pcm_s16le", str(output_wav)])


def write_slideshow_concat(frames: list[Path], durations: list[float], out_path: Path, fps: int) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    min_dur = 1.0 / fps
    with out_path.open("w", encoding="utf-8") as f:
        for frame, duration in zip(frames, durations):
            f.write(f"file '{frame.resolve().as_posix()}'\n")
            f.write(f"duration {max(duration, min_dur):.6f}\n")
        f.write(f"file '{frames[-1].resolve().as_posix()}'\n")


def build_video_only(frames: list[Path], durations: list[float], concat_list: Path,
                     output_path: Path, total_duration: float, fps: int, enc: dict) -> None:
    write_slideshow_concat(frames, durations, concat_list, fps)
    use_nvenc = nvenc_available()
    video_codec = "h264_nvenc" if use_nvenc else "libx264"
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
           "-vsync", "cfr", "-r", str(fps), "-t", f"{total_duration + 0.5:.6f}",
           "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart",
           "-video_track_timescale", "90000"]
    if use_nvenc:
        cmd += ["-c:v", "h264_nvenc",
                "-cq",     str(enc.get("nvenc_cq", 19)),
                "-preset", enc.get("nvenc_preset", "p6")]
    else:
        cmd += ["-c:v", "libx264",
                "-crf",    str(enc.get("libx264_crf", 18)),
                "-preset", enc.get("libx264_preset", "slow")]
    cmd.append(str(output_path))
    run(cmd)


def mux_video_and_audio(video_only: Path, chapter_audio: Path, output_path: Path,
                        aac_bitrate: str, sample_rate: int) -> None:
    run(["ffmpeg", "-y", "-i", str(video_only), "-i", str(chapter_audio),
         "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "copy", "-c:a", "aac", "-b:a", aac_bitrate, "-ar", str(sample_rate),
         "-movflags", "+faststart", str(output_path)])


def main() -> None:
    paths  = load_paths()
    syscfg = load_system_config()

    vid_cfg  = syscfg.get("video", {})
    aud_cfg  = syscfg.get("audio", {})
    enc_cfg  = vid_cfg.get("encoder", {})
    ln_cfg   = aud_cfg.get("loudnorm", {})

    fps         = int(vid_cfg.get("fps",          24))
    sample_rate = int(aud_cfg.get("sample_rate",  48000))
    channels    = int(aud_cfg.get("channels",     1))
    aac_bitrate = str(enc_cfg.get("aac_bitrate",  "256k"))
    lufs = float(ln_cfg.get("integrated_lufs",   -14.0))
    tp   = float(ln_cfg.get("true_peak_db",      -1.5))
    lra  = float(ln_cfg.get("loudness_range_lu", 11.0))

    if not paths["image_root"].exists():
        raise SystemExit(f"[FATAL] Panel folder not found: {paths['image_root']}")
    faded = paths["faded_audio_root"]
    raw   = paths["raw_audio_root"]
    if faded.exists() and any(faded.iterdir()):
        audio_root = faded
    elif raw.exists() and any(raw.iterdir()):
        audio_root = raw
    else:
        raise SystemExit(f"[FATAL] No audio found in {faded} or {raw}")

    print(f"[INFO] Audio source: {audio_root.name}")

    for d in (paths["frames_dir"], paths["pcm_dir"], paths["lists_dir"], paths["build_dir"]):
        d.mkdir(parents=True, exist_ok=True)

    pairs = collect_pairs(paths["image_root"], audio_root)
    if not pairs:
        raise SystemExit("[FATAL] No matched image/audio pairs found.")

    print(f"[INFO] Manga: {paths['name']}  Chapter: {paths['chapter']:02d}")
    print(f"[INFO] Pairs: {len(pairs)}")
    wm_cfg = syscfg.get("watermark", {})

    print(f"[INFO] Resolution: {paths['target_res'][0]}x{paths['target_res'][1]}  FPS: {fps}")
    print(f"[INFO] Watermark: {wm_cfg.get('enabled', False)}")

    prerender_frames(
        pairs,
        paths["frames_dir"],
        paths["target_res"],
        paths["background_image"],
        paths["video_config"],
        wm_cfg,
    )

    pcm_paths    = build_pcm_clips(pairs, paths["pcm_dir"], sample_rate, channels)
    joined_audio = paths["build_dir"] / "chapter_joined.wav"
    loud_audio   = paths["build_dir"] / "chapter_joined_loudnorm.wav"
    audio_concat = paths["lists_dir"] / "audio_concat.txt"
    join_pcm_audio(pcm_paths, joined_audio, audio_concat)
    loudnorm_wav(joined_audio, loud_audio, lufs, tp, lra, sample_rate, channels)

    frame_paths  = [paths["frames_dir"] / f"{image.stem}.png" for image, _ in pairs]
    durations    = [ffprobe_duration(audio) for _, audio in pairs]
    total_dur    = sum(durations)
    video_concat = paths["lists_dir"] / "video_concat.txt"
    video_only   = paths["build_dir"] / "chapter_video_only.mp4"
    build_video_only(frame_paths, durations, video_concat, video_only, total_dur, fps, enc_cfg)

    mux_video_and_audio(video_only, loud_audio, paths["output_path"], aac_bitrate, sample_rate)
    print(f"[DONE] {paths['output_path']}")

    keep_tmp = bool(syscfg.get("render", {}).get("keep_tmp", False))
    tmp_root = paths["frames_dir"].parent
    if keep_tmp:
        print(f"[INFO] Keeping tmp files at: {tmp_root}  (render.keep_tmp=true)")
    else:
        try:
            shutil.rmtree(tmp_root)
            print(f"[INFO] Removed tmp folder: {tmp_root}")
        except Exception as exc:
            print(f"[WARN] Could not remove tmp folder: {exc}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or exc.stdout or str(exc), file=sys.stderr)
        raise
