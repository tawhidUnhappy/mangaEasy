from __future__ import annotations

import argparse
from pathlib import Path

from mangaeasy.video_pipeline.common import DEFAULT_OUTPUT_ROOT, DEFAULT_PROJECT_ROOT, DEFAULT_WORK_DIR
from mangaeasy.video_pipeline.long_video_builder import LongVideoConfig, build_long_video


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Join rendered item videos into one long video.")
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--input-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--start", default="01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--items", nargs="*", help="Item names or ranges. Long video expects a continuous range.")
    parser.add_argument("--item-range", help="Convenience range, for example: 01-12.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--reencode", action="store_true")
    parser.add_argument("--copy-all", action="store_true")
    parser.add_argument("--encoder", default="auto")
    parser.add_argument("--preset", default="p1")
    parser.add_argument("--cq", type=int, default=18)
    parser.add_argument("--audio-bitrate", default="128k")
    parser.add_argument("--narration-dir", type=Path, default=None)
    parser.add_argument("--background-music", type=Path, default=None)
    parser.add_argument("--music-volume", type=float, default=0.035)
    parser.add_argument("--narration-volume", type=float, default=1.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    build_long_video(
        LongVideoConfig(
            project_root=args.project_root,
            project_name_override=args.project_name,
            output_root=args.output_root,
            input_dir=args.input_dir,
            output=args.output,
            work_dir=args.work_dir,
            start=args.start,
            end=args.end,
            items=args.items,
            item_range=args.item_range,
            overwrite=args.overwrite,
            reencode=args.reencode,
            copy_all=args.copy_all,
            encoder=args.encoder,
            preset=args.preset,
            cq=args.cq,
            audio_bitrate=args.audio_bitrate,
            narration_dir=args.narration_dir,
            background_music=args.background_music,
            music_volume=args.music_volume,
            narration_volume=args.narration_volume,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
