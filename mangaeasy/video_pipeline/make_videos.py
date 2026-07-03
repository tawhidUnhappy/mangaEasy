from __future__ import annotations

import argparse
from pathlib import Path

from mangaeasy.utils import emit_result
from mangaeasy.video_pipeline.common import DEFAULT_AUDIO_ROOT, DEFAULT_OUTPUT_ROOT, DEFAULT_PROJECT_ROOT, DEFAULT_WORK_DIR
from mangaeasy.video_pipeline.item_video_builder import VideoBuildConfig, build_item_videos


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build GPU-encoded videos from image panels and matching audio.")
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--items", nargs="*", help="Item folder names or ranges, for example: 01 02 05-08.")
    parser.add_argument("--item-range", help="Convenience range, for example: 01-12.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--encoder", default="auto")
    parser.add_argument("--preset", default="p1")
    parser.add_argument("--cq", type=int, default=18)
    parser.add_argument("--audio-bitrate", default="128k")
    parser.add_argument("--background-style", choices=("blur", "black", "image"), default="blur")
    parser.add_argument("--background-image", type=Path, default=None)
    parser.add_argument("--blur-sigma", type=float, default=28.0)
    parser.add_argument("--blur-downscale", type=int, default=4)
    parser.add_argument("--blur-backend", choices=("auto", "vulkan", "cpu"), default="auto")
    parser.add_argument("--background-brightness", type=float, default=-0.06)
    parser.add_argument("--background-saturation", type=float, default=1.08)
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument("--render-mode", choices=("segments", "concat-images"), default="segments")
    parser.add_argument("--workers", type=int, default=3,
                         help="Number of item folders to render in parallel. NVENC consumer GPUs "
                              "typically cap at ~3 concurrent encode sessions, so going much "
                              "higher than that won't add throughput.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = build_item_videos(
        VideoBuildConfig(
            project_root=args.project_root,
            audio_root=args.audio_root,
            output_root=args.output_root,
            output_dir=args.output_dir,
            project_name_override=args.project_name,
            work_dir=args.work_dir,
            items=args.items,
            item_range=args.item_range,
            overwrite=args.overwrite,
            width=args.width,
            height=args.height,
            fps=args.fps,
            encoder=args.encoder,
            preset=args.preset,
            cq=args.cq,
            audio_bitrate=args.audio_bitrate,
            background_style=args.background_style,
            background_image=args.background_image.resolve() if args.background_image else None,
            blur_sigma=args.blur_sigma,
            blur_downscale=args.blur_downscale,
            blur_backend=args.blur_backend,
            background_brightness=args.background_brightness,
            background_saturation=args.background_saturation,
            keep_work=args.keep_work,
            render_mode=args.render_mode,
            workers=args.workers,
        )
    )
    emit_result(
        output_dir=output_dir,
        outputs=sorted(str(p) for p in output_dir.glob("item_*.mp4")),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
