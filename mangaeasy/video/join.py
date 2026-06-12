#!/usr/bin/env python3
"""mangaeasy.video.join — join all chapter videos into one long video.

mangaeasy join-chapters  (main)       — rebuild from raw panels + audio, then add BGM
mangaeasy join-chapters-nobgm         (main_nobgm) — concat existing chapter videos, no BGM
"""

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageOps

from mangaeasy.config import PROJECT_ROOT, load_download_config, load_system_config
from mangaeasy.images.watermark_util import apply_watermark
from mangaeasy.paths import manga_dir
from mangaeasy.video.render import (
    collect_pairs,
    build_pcm_clips,
    join_pcm_audio,
    loudnorm_wav,
    build_video_only,
    mux_video_and_audio,
    ffprobe_duration,
)

TMP_DIR = PROJECT_ROOT / "tmp"


def _sorted_chapter_dirs(manga_root: Path) -> list[Path]:
    return sorted(
        [d for d in manga_root.iterdir() if d.is_dir() and d.name[0].isdigit()],
        key=lambda p: int(p.name) if p.name.isdigit() else p.name,
    )


def _collect_videos(manga_root: Path) -> tuple[list[Path], list[Path]]:
    chapter_dirs = _sorted_chapter_dirs(manga_root)
    videos: list[Path] = []
    for ch in chapter_dirs:
        mp4s = list(ch.glob("*.mp4"))
        if mp4s:
            videos.append(mp4s[0])
    return videos, chapter_dirs


def _write_concat_list(videos: list[Path], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for v in videos:
            f.write(f"file '{v.resolve()}'\n")


def _setup_join(suffix: str = "") -> tuple[str, Path, list[Path], Path, Path]:
    dl   = load_download_config()
    name = str(dl["name"])
    manga_root = manga_dir(name)

    if not manga_root.exists():
        print(f"[ERROR] Manga folder not found: {manga_root}")
        sys.exit(1)

    videos, chapter_dirs = _collect_videos(manga_root)
    if not videos:
        print("[ERROR] No chapter videos found.")
        sys.exit(1)

    start = chapter_dirs[0].name
    end   = chapter_dirs[-1].name
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    output_file = TMP_DIR / f"{start}_{end}_{name}{suffix}.mp4"
    concat_list = TMP_DIR / "concat_list.txt"
    _write_concat_list(videos, concat_list)
    return name, manga_root, videos, output_file, concat_list


def main() -> None:
    """Rebuild full video from scratch: panels + audio from every chapter, then add BGM.

    Treats every panel/audio pair from every chapter as one continuous stream —
    no pre-made chapter videos involved, so there are no chapter-boundary glitches.
    """
    syscfg = load_system_config()
    dl     = load_download_config()
    name   = str(dl["name"])
    manga_root = manga_dir(name)

    if not manga_root.exists():
        print(f"[ERROR] Manga folder not found: {manga_root}")
        sys.exit(1)

    # ── Config ────────────────────────────────────────────────────────────────
    vid_cfg  = syscfg.get("video", {})
    aud_cfg  = syscfg.get("audio", {})
    enc_cfg  = vid_cfg.get("encoder", {})
    ln_cfg   = aud_cfg.get("loudnorm", {})
    bgm_cfg  = syscfg.get("bgm", {})
    path_cfg = syscfg.get("paths", {})

    fps         = int(vid_cfg.get("fps",          24))
    w           = int(vid_cfg.get("width",        1920))
    h           = int(vid_cfg.get("height",       1080))
    target_res  = (w, h)
    sample_rate = int(aud_cfg.get("sample_rate",  48000))
    channels    = int(aud_cfg.get("channels",     1))
    aac_bitrate = str(enc_cfg.get("aac_bitrate",  "256k"))
    lufs = float(ln_cfg.get("integrated_lufs",   -14.0))
    tp   = float(ln_cfg.get("true_peak_db",      -1.5))
    lra  = float(ln_cfg.get("loudness_range_lu", 11.0))

    bg_img_rel    = vid_cfg.get("background_image", "background_image/grid_background_1920x1080.png")
    bg_img        = PROJECT_ROOT / bg_img_rel
    wm_cfg        = syscfg.get("watermark", {})
    bg_music      = PROJECT_ROOT / bgm_cfg.get("file", "music/bgm.mp3")
    bg_vol        = float(bgm_cfg.get("volume_db", -32))
    audio_bitrate = str(enc_cfg.get("bgm_bitrate", "192k"))

    panels_subdir    = path_cfg.get("panels_subdir",    "panels")
    processed_subdir = path_cfg.get("processed_subdir", "panels_processed")
    audio_subdir     = path_cfg.get("audio_subdir",     "audio")
    upscale_on       = bool(syscfg.get("process_panels", {}).get("upscale", True))

    if not bg_music.exists():
        print(f"[ERROR] Background music not found: {bg_music}")
        sys.exit(1)

    # ── Collect all (panel, audio) pairs from every chapter ───────────────────
    ch_dirs = _sorted_chapter_dirs(manga_root)
    if not ch_dirs:
        print("[ERROR] No chapter directories found.")
        sys.exit(1)

    all_pairs: list[tuple[Path, Path]] = []
    for ch_dir in ch_dirs:
        proc_dir  = ch_dir / processed_subdir
        raw_dir   = ch_dir / panels_subdir
        faded_dir = ch_dir / "audio_faded"
        raw_audio = ch_dir / audio_subdir

        image_root = (
            proc_dir if upscale_on and proc_dir.exists() and any(proc_dir.iterdir())
            else raw_dir
        )
        # prefer faded audio (fade-in/out applied), fall back to raw
        audio_root = (
            faded_dir if faded_dir.exists() and any(faded_dir.iterdir())
            else raw_audio
        )

        if not image_root.exists() or not audio_root.exists():
            print(f"[WARN] Chapter {ch_dir.name}: missing panels or audio, skipping.")
            continue

        pairs = collect_pairs(image_root, audio_root)
        if not pairs:
            print(f"[WARN] Chapter {ch_dir.name}: no matched pairs, skipping.")
            continue

        all_pairs.extend(pairs)
        print(f"[INFO] Chapter {ch_dir.name}: {len(pairs)} pairs  (audio: {audio_root.name})")

    if not all_pairs:
        print("[ERROR] No matched panel/audio pairs found across any chapter.")
        sys.exit(1)

    start = ch_dirs[0].name
    end   = ch_dirs[-1].name
    output_file = TMP_DIR / f"{start}_{end}_{name}_bgm.mp4"
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    join_tmp   = TMP_DIR / "_join_scratch"
    frames_dir = join_tmp / "frames"
    pcm_dir    = join_tmp / "pcm"
    lists_dir  = join_tmp / "lists"
    build_dir  = join_tmp / "build"
    for d in (frames_dir, pcm_dir, lists_dir, build_dir):
        d.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Total pairs: {len(all_pairs)}  Chapters: {start}–{end}")
    print(f"[INFO] Resolution: {w}x{h}  FPS: {fps}")

    # ── Pre-render frames ─────────────────────────────────────────────────────
    # Global index prefix avoids name collisions when chapters share panel filenames.
    if bg_img.exists():
        with Image.open(bg_img).convert("RGB") as bg:
            canvas = ImageOps.fit(bg, target_res, Image.LANCZOS).copy()
    else:
        canvas = Image.new("RGB", target_res, (0, 0, 0))

    frame_paths: list[Path] = []
    print(f"[INFO] Pre-rendering {len(all_pairs)} frames...")
    for idx, (image, _) in enumerate(all_pairs, start=1):
        out = frames_dir / f"{idx:06d}_{image.stem}.png"
        with Image.open(image).convert("RGBA") as img:
            fitted = ImageOps.contain(img, target_res, Image.LANCZOS)
            frame = canvas.copy()
            x = (target_res[0] - fitted.width) // 2
            y = (target_res[1] - fitted.height) // 2
            frame.paste(fitted, (x, y), fitted)
            if wm_cfg and wm_cfg.get("enabled"):
                frame = apply_watermark(frame, wm_cfg)
            frame.save(out, "PNG", optimize=True)
        frame_paths.append(out)
        if idx % 50 == 0 or idx == len(all_pairs):
            print(f"[FRAME] {idx}/{len(all_pairs)}")

    # ── Normalize all audio clips to PCM ──────────────────────────────────────
    pcm_paths = build_pcm_clips(all_pairs, pcm_dir, sample_rate, channels)

    # ── Join all PCM clips into one WAV + loudnorm ────────────────────────────
    joined_audio = build_dir / "joined.wav"
    loud_audio   = build_dir / "joined_loudnorm.wav"
    audio_concat = lists_dir / "audio_concat.txt"
    join_pcm_audio(pcm_paths, joined_audio, audio_concat)
    loudnorm_wav(joined_audio, loud_audio, lufs, tp, lra, sample_rate, channels)

    # ── Build video-only slideshow ────────────────────────────────────────────
    durations    = [ffprobe_duration(audio) for _, audio in all_pairs]
    total_dur    = sum(durations)
    video_concat = lists_dir / "video_concat.txt"
    video_only   = build_dir / "video_only.mp4"
    build_video_only(frame_paths, durations, video_concat, video_only, total_dur, fps, enc_cfg)

    # ── Mux video + narration audio ───────────────────────────────────────────
    joined_raw = build_dir / "joined_raw.mp4"
    mux_video_and_audio(video_only, loud_audio, joined_raw, aac_bitrate, sample_rate)

    # ── Overlay looping BGM ───────────────────────────────────────────────────
    print("[INFO] Adding looping background music...")
    cmd_bgm = [
        "ffmpeg", "-y",
        "-i", str(joined_raw),
        "-stream_loop", "-1", "-i", str(bg_music),
        "-filter_complex",
        f"[1:a]volume={bg_vol}dB[bgm];[0:a][bgm]amix=inputs=2:normalize=0[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", audio_bitrate, "-shortest",
        str(output_file),
    ]
    subprocess.run(cmd_bgm, check=True)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    keep_tmp = bool(syscfg.get("render", {}).get("keep_tmp", False))
    if keep_tmp:
        print(f"[INFO] Keeping tmp files at: {join_tmp}  (render.keep_tmp=true)")
    else:
        shutil.rmtree(join_tmp, ignore_errors=True)
        print(f"[INFO] Removed tmp folder: {join_tmp}")

    print(f"[DONE] {output_file}")


def main_nobgm() -> None:
    """Join chapters without background music (mangaeasy join-chapters-nobgm)."""
    _, _, _, output_file, concat_list = _setup_join("_nobgm")

    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
           "-i", str(concat_list), "-c", "copy", str(output_file)]
    print("[INFO] Joining chapter videos (no background music)...")
    subprocess.run(cmd, check=True)
    concat_list.unlink(missing_ok=True)
    print(f"[DONE] {output_file}")


if __name__ == "__main__":
    main()
