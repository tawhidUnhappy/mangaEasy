from __future__ import annotations

import argparse
import subprocess
from datetime import datetime
from pathlib import Path

from mangaeasy.utils import archive_before_overwrite, emit_result
from mangaeasy.video_pipeline.common import (
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PROJECT_ROOT,
    find_latest_long_video,
    project_name,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mix background music into an already-joined long video, without rebuilding it from item clips."
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--input", type=Path, default=None,
                        help="Long video to add music to (default: the project's joined long video).")
    parser.add_argument("--output", type=Path, default=None,
                        help="Where to write the mixed video (default: a new file next to --input, named with "
                             "the music volume and a timestamp, so trying different mixes never overwrites a "
                             "previous one).")
    parser.add_argument("--replace", action="store_true",
                        help="Overwrite --input in place instead of writing a new file (the previous file is "
                             "archived first, same as other generation steps).")
    parser.add_argument("--background-music", type=Path, required=True)
    parser.add_argument("--music-volume-db", type=float, default=-25.0,
                        help="Background music loudness in dB (negative = quieter), applied via ffmpeg's volume filter.")
    parser.add_argument("--narration-volume", type=float, default=1.0)
    parser.add_argument("--duck", action="store_true",
                        help="Enable audio ducking: background music is automatically lowered "
                             "whenever the narration is audible, so the narration is never "
                             "drowned out. Uses ffmpeg sidechaincompress internally.")
    parser.add_argument("--duck-ratio", type=float, default=10.0,
                        help="Compression ratio for ducking (1–20). Higher = music ducks more aggressively.")
    parser.add_argument("--duck-attack", type=float, default=5.0,
                        help="How fast (ms) the music ducks when narration starts.")
    parser.add_argument("--duck-release", type=float, default=500.0,
                        help="How fast (ms) the music fades back up when narration stops.")
    parser.add_argument("--audio-bitrate", default="192k")
    return parser.parse_args()


def default_bgm_output(video_in: Path, music_volume_db: float) -> Path:
    """A sibling filename that encodes the music volume and a timestamp.

    Default behavior never overwrites a previous mix: each run of this
    command produces its own file next to the clean joined video, so a user
    comparing several background-music takes (different tracks/volumes) ends
    up with all of them on disk, distinguishable by name alone, instead of
    one file silently replaced each time (or buried in old/run_NNNN/).
    """
    sign = "p" if music_volume_db >= 0 else "m"
    volume_tag = f"{sign}{abs(music_volume_db):g}dB".replace(".", "_")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return video_in.with_name(f"{video_in.stem}_bgm_{volume_tag}_{timestamp}{video_in.suffix}")


def add_background_music(
    video_in: Path, video_out: Path, music_file: Path, music_volume_db: float, narration_volume: float, audio_bitrate: str,
    duck: bool = False, duck_ratio: float = 10.0, duck_attack: float = 5.0, duck_release: float = 500.0,
) -> Path:
    if not video_in.is_file():
        raise FileNotFoundError(f"Long video not found: {video_in}. Run the join step first.")
    if not music_file.is_file():
        raise FileNotFoundError(f"Background music not found: {music_file}")

    if video_out == video_in:
        source = archive_before_overwrite(video_in)
        assert source is not None  # video_in.is_file() was just checked above
        print(f"Archived previous long video to: {source}", flush=True)
    else:
        source = video_in
        video_out.parent.mkdir(parents=True, exist_ok=True)

    if duck:
        # Sidechain compress: narration is the sidechain signal that triggers
        # the music to duck. When narration is audible the music volume drops
        # automatically, preventing it from overpowering the narration.
        filter_complex = (
            f"[0:a]volume={narration_volume},aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[narr];"
            f"[1:a]volume={music_volume_db}dB,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[music];"
            "[narr]asplit=2[narr_main][narr_sc];"
            f"[music][narr_sc]sidechaincompress=threshold=0.01:ratio={duck_ratio}:attack={duck_attack}:release={duck_release}[music_ducked];"
            "[narr_main][music_ducked]amix=inputs=2:duration=first:dropout_transition=3,"
            "alimiter=limit=0.95,aresample=async=1:first_pts=0[a]"
        )
    else:
        filter_complex = (
            f"[0:a]volume={narration_volume},aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[narr];"
            f"[1:a]volume={music_volume_db}dB,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[music];"
            "[narr][music]amix=inputs=2:duration=first:dropout_transition=3,"
            "alimiter=limit=0.95,aresample=async=1:first_pts=0[a]"
        )
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-guess_layout_max", "0", "-stream_loop", "-1", "-i", str(music_file),
        "-filter_complex", filter_complex,
        "-map", "0:v:0", "-map", "[a]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-movflags", "+faststart",
        str(video_out),
    ]
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)
    return video_out


def resolve_default_input(output_root: Path, name: str) -> Path:
    found = find_latest_long_video(output_root, name)
    if found is None:
        raise FileNotFoundError(
            f"No joined long video found for '{name}' under {(output_root / name).resolve()}. "
            "Run the join step first."
        )
    return found


def main() -> int:
    args = parse_args()
    name = project_name(args.project_root, args.project_name)
    video_in = (args.input or resolve_default_input(args.output_root, name)).resolve()
    if args.replace:
        video_out = video_in
    else:
        video_out = (args.output or default_bgm_output(video_in, args.music_volume_db)).resolve()
    add_background_music(
        video_in, video_out, args.background_music, args.music_volume_db, args.narration_volume, args.audio_bitrate,
        duck=args.duck, duck_ratio=args.duck_ratio, duck_attack=args.duck_attack, duck_release=args.duck_release,
    )
    print(f"\nAdded background music: {video_out}", flush=True)
    emit_result(outputs=[video_out])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
