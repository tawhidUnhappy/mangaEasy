from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
from pathlib import Path

from mangaeasy.video_pipeline.common import DEFAULT_OUTPUT_ROOT, DEFAULT_PROJECT_ROOT, project_name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize the final long video's audio for YouTube-friendly loudness."
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--replace", action="store_true", help="Replace input after writing through a temporary file.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--target-i", type=float, default=-14.0, help="Integrated loudness target in LUFS.")
    parser.add_argument("--target-tp", type=float, default=-1.5, help="True peak target in dBTP.")
    parser.add_argument("--target-lra", type=float, default=11.0, help="Loudness range target.")
    parser.add_argument("--audio-bitrate", default="192k")
    parser.add_argument("--sample-rate", type=int, default=48000)
    return parser.parse_args()


def run(command: list[str], capture: bool = False) -> subprocess.CompletedProcess[str]:
    print(" ".join(shlex.quote(part) for part in command), flush=True)
    return subprocess.run(
        command,
        check=True,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def default_input(args: argparse.Namespace) -> Path:
    name = project_name(args.project_root, args.project_name)
    return args.output_root.resolve() / name / f"{name}_full.mp4"


def default_output(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_youtube_audio.mp4")


def loudnorm_base(args: argparse.Namespace) -> str:
    return f"loudnorm=I={args.target_i}:TP={args.target_tp}:LRA={args.target_lra}"


def parse_loudnorm_json(stderr: str) -> dict[str, str]:
    matches = re.findall(r"\{\s*\"input_i\".*?\}", stderr, flags=re.DOTALL)
    if not matches:
        raise ValueError("Could not find loudnorm JSON in ffmpeg output.")
    data = json.loads(matches[-1])
    required = ["input_i", "input_tp", "input_lra", "input_thresh", "target_offset"]
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError("loudnorm output missing keys: " + ", ".join(missing))
    return {key: str(data[key]) for key in required}


def first_pass(
    input_path: Path,
    args: argparse.Namespace,
) -> dict[str, str]:
    audio_filter = f"{loudnorm_base(args)}:print_format=json"
    result = run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(input_path),
            "-map", "0:a:0",
            "-af", audio_filter,
            "-f", "null", "-",
        ],
        capture=True,
    )
    return parse_loudnorm_json(result.stderr)


def second_pass(
    input_path: Path,
    output_path: Path,
    measured: dict[str, str],
    args: argparse.Namespace,
) -> None:
    filter_arg = (
        f"{loudnorm_base(args)}:"
        f"measured_I={measured['input_i']}:"
        f"measured_TP={measured['input_tp']}:"
        f"measured_LRA={measured['input_lra']}:"
        f"measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}:"
        "linear=true:print_format=summary,"
        f"aresample={args.sample_rate}:async=1:first_pts=0"
    )
    command = [
        "ffmpeg", "-hide_banner", "-y" if args.overwrite or args.replace else "-n",
        "-i", str(input_path),
        "-map", "0:v:0", "-map", "0:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", args.audio_bitrate,
        "-af", filter_arg,
        "-movflags", "+faststart",
        str(output_path),
    ]
    run(command)


def main() -> int:
    args = parse_args()
    input_path = (args.input or default_input(args)).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input video not found: {input_path}")

    if args.replace:
        output_path = input_path.with_name(f"{input_path.stem}.normalizing.tmp.mp4")
        args.overwrite = True
    else:
        output_path = (args.output or default_output(input_path)).resolve()
        if output_path == input_path:
            raise ValueError("Output must differ from input unless --replace is used.")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(
        f"Normalizing audio to I={args.target_i} LUFS, TP={args.target_tp} dBTP, "
        f"LRA={args.target_lra}. Video stream will be copied.",
        flush=True,
    )
    measured = first_pass(input_path, args)
    print("Measured loudness:", measured, flush=True)
    second_pass(input_path, output_path, measured, args)

    if args.replace:
        backup_path = input_path.with_name(f"{input_path.stem}.before_normalize.mp4")
        if backup_path.exists():
            backup_path.unlink()
        input_path.replace(backup_path)
        output_path.replace(input_path)
        print(f"\nReplaced input: {input_path}", flush=True)
        print(f"Backup written: {backup_path}", flush=True)
    else:
        print(f"\nNormalized video written to: {output_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
