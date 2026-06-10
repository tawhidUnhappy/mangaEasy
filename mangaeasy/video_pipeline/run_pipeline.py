from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from mangaeasy.video_pipeline.common import (
    DEFAULT_AUDIO_ROOT,
    DEFAULT_KOKORO_ROOT,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PROJECT_ROOT,
    DEFAULT_WORK_DIR,
    merge_item_selection,
    project_name,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Kokoro audio generation, then build videos.")
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--kokoro-root", type=Path, default=DEFAULT_KOKORO_ROOT)
    parser.add_argument("--items", nargs="*", help="Item names or ranges, for example: 01 02 05-08.")
    parser.add_argument("--item-range", help="Convenience range, for example: 01-12.")
    parser.add_argument("--overwrite-audio", action="store_true")
    parser.add_argument("--overwrite-video", action="store_true")
    parser.add_argument("--voice", default="af_heart")
    parser.add_argument("--lang", default="a")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--build-long-video", action="store_true")
    parser.add_argument("--background-style", choices=("blur", "black", "image"), default="black")
    parser.add_argument("--background-image", type=Path, default=None)
    parser.add_argument("--background-music", type=Path, default=None)
    parser.add_argument("--music-volume", type=float, default=0.035)
    parser.add_argument("--narration-volume", type=float, default=1.0)
    parser.add_argument("--audio-bitrate", default="128k")
    parser.add_argument("--render-mode", choices=("segments", "concat-images"), default="segments")
    parser.add_argument("--encoder", default="auto")
    parser.add_argument("--video-preset", default="p1")
    parser.add_argument("--cq", type=int, default=18)
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--video-workers", type=int, default=1)
    return parser.parse_args()


def run(command: list[str], cwd: Path) -> None:
    print(f"\n[{cwd}] {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    args = parse_args()
    cwd = Path.cwd()
    selected_items = merge_item_selection(args.items, args.item_range)

    audio_cmd = [
        sys.executable, "-m", "mangaeasy.video_pipeline.generate_audio",
        "--project-root", str(args.project_root),
        "--audio-root", str(args.audio_root),
        "--work-dir", str(args.work_dir),
        "--kokoro-root", str(args.kokoro_root),
        "--voice", args.voice,
        "--lang", args.lang,
        "--speed", str(args.speed),
        "--device", args.device,
    ]
    if args.project_name:
        audio_cmd += ["--project-name", args.project_name]
    if args.overwrite_audio:
        audio_cmd.append("--overwrite")
    if selected_items:
        audio_cmd += ["--items", *selected_items]

    video_cmd = [
        sys.executable, "-m", "mangaeasy.video_pipeline.make_videos",
        "--project-root", str(args.project_root),
        "--audio-root", str(args.audio_root),
        "--output-root", str(args.output_root),
        "--work-dir", str(args.work_dir),
        "--background-style", args.background_style,
        "--audio-bitrate", args.audio_bitrate,
        "--render-mode", args.render_mode,
        "--encoder", args.encoder,
        "--preset", args.video_preset,
        "--cq", str(args.cq),
        "--fps", str(args.fps),
        "--workers", str(args.video_workers),
    ]
    if args.project_name:
        video_cmd += ["--project-name", args.project_name]
    if args.background_image is not None:
        video_cmd += ["--background-image", str(args.background_image)]
    if args.overwrite_video:
        video_cmd.append("--overwrite")
    if selected_items:
        video_cmd += ["--items", *selected_items]

    run(audio_cmd, cwd)
    run(video_cmd, cwd)
    if args.build_long_video:
        name = project_name(args.project_root, args.project_name)
        long_cmd = [
            sys.executable, "-m", "mangaeasy.video_pipeline.make_long_video",
            "--project-root", str(args.project_root),
            "--output-root", str(args.output_root),
            "--work-dir", str(args.work_dir),
            "--narration-dir", str(args.audio_root.resolve() / name / "_items"),
            "--audio-bitrate", args.audio_bitrate,
            "--overwrite",
        ]
        if args.project_name:
            long_cmd += ["--project-name", args.project_name]
        if args.background_music is not None:
            long_cmd += [
                "--background-music", str(args.background_music),
                "--music-volume", str(args.music_volume),
                "--narration-volume", str(args.narration_volume),
            ]
        if selected_items:
            long_cmd += ["--items", *selected_items]
        run(long_cmd, cwd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
