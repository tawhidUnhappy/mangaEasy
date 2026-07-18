from __future__ import annotations

import argparse
import math

from mediaconductor import runtime
from datetime import datetime
from pathlib import Path

from mediaconductor.defaults import (
    configured_background_music,
    default_music_volume_db,
)
from mediaconductor.utils import archive_before_overwrite, emit_result
from mediaconductor.video_pipeline.common import (
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PROJECT_ROOT,
    DEFAULT_WORK_DIR,
    find_latest_long_video,
    project_name,
)

DEFAULT_EXISTING_VIDEO_NARRATION_VOLUME = 1.0


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
    parser.add_argument("--background-music", type=Path, default=None,
                        help="Music file to mix in. Defaults to config.system.json -> bgm.file "
                             "or the tracked default music asset.")
    parser.add_argument("--raw-music", action="store_true",
                        help="Mix the music file exactly as given. By default the track is QC'd and, when it "
                             "has splice holes, silent lead/tail, or is shorter than the video, replaced by a "
                             "repaired seamless crossfaded bed (cached under <work-dir>/music_bed/) so raw "
                             "-stream_loop seams and in-track defects can't repeat through the whole video.")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR,
                        help="Scratch dir for the cached music bed (default: the pipeline work dir).")
    parser.add_argument("--music-volume-db", type=float, default=default_music_volume_db(),
                        help="How far the music sits below the narration, in dB (negative = quieter). The music "
                             "stem is loudness-aligned to the joined narration first (see "
                             "--no-music-loudnorm), so this value is a true LU separation regardless of how hot "
                             "the source track was mastered. Default -28 keeps the bed comfortable for long "
                             "dense, wall-to-wall recap narration without fatiguing the listener; -26 to -22 "
                             "suits punchier or sparser edits that want the bed to read more; below -32 the "
                             "music risks becoming inaudible on phone speakers.")
    parser.add_argument("--no-music-loudnorm", action="store_true",
                        help="Skip measuring both stems and aligning music to narration before applying "
                             "--music-volume-db. With this flag the offset is applied "
                             "to the raw file, so the effective separation depends on the track's mastering.")
    parser.add_argument("--condition-bed", action=argparse.BooleanOptionalAction, default=True,
                        help="Compress the music's dynamic range so it sits at a consistent level under the "
                             "narration instead of swelling and receding on its own (a raw track's 6-10 LU "
                             "loudness range is the top reason a bed sounds 'unmixed'). On by default; "
                             "--no-condition-bed applies only the flat dB offset.")
    parser.add_argument("--eq-carve", action=argparse.BooleanOptionalAction, default=True,
                        help="Gently dip the music in the 2-5 kHz speech-intelligibility band so it masks the "
                             "voice less (part of bed conditioning). On by default; --no-eq-carve keeps the "
                             "music's full spectrum.")
    parser.add_argument("--narration-volume", type=float, default=DEFAULT_EXISTING_VIDEO_NARRATION_VOLUME,
                        help="Narration gain before mixing. Joined videos already contain their configured "
                             "voice gain, so the standalone default is 1.0 (unity). The full pipeline joins "
                             "at unity and passes its configured lift here, ensuring gain is applied once.")
    parser.add_argument("--duck", action=argparse.BooleanOptionalAction, default=True,
                        help="Sidechain-duck the music under the narration: the bed automatically dips a few dB "
                             "whenever the voice is present and breathes back up in the pauses — the standard "
                             "radio/podcast/DaVinci workflow. On by default; --no-duck holds the music at a "
                             "flat level.")
    parser.add_argument("--duck-ratio", type=float, default=2.0,
                        help="Compression ratio for ducking (1–20). Higher = music ducks more aggressively. "
                             "Default 2 gives a gentle ~3-4 dB dip during speech that breathes up in the gaps. "
                             "For wall-to-wall narration a low ratio matters: a high ratio just makes the music "
                             "uniformly quiet (ducking degenerates to a constant reduction) instead of dipping.")
    parser.add_argument("--duck-attack", type=float, default=20.0,
                        help="How fast (ms) the music ducks when narration starts.")
    parser.add_argument("--duck-release", type=float, default=350.0,
                        help="How fast (ms) the music fades back up when narration stops. Too short pumps on "
                             "the micro-gaps between words; 300-400 ms rides sentence pauses smoothly.")
    parser.add_argument("--duck-threshold", type=float, default=0.08,
                        help="Sidechain level (linear, 0-1) above which the narration triggers ducking. "
                             "Default 0.08 (~-22 dBFS) catches speech while keeping the dip gentle.")
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


_AFMT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


def build_mix_filter(
    *, narration_volume: float, music_volume_db: float,
    duck: bool = True, duck_ratio: float = 2.0, duck_attack: float = 20.0,
    duck_release: float = 350.0, duck_threshold: float = 0.08,
) -> str:
    """Build the ffmpeg -filter_complex string mixing narration (input 0) and
    music (input 1) into label [a]. Pure — unit-tested in test_music_bed.py.

    Two invariants baked in and load-bearing (see CLAUDE.md):
    - ``amix=...:normalize=0`` — amix's default rescales every input by
      1/inputs (-6 dB for two), which would silently undo the narration's
      -14 LUFS normalization. Plain summation keeps the voice at target.
    - ``alimiter=level=disabled`` — alimiter's default ``level=true``
      auto-normalizes the output back toward 0 dBFS, fighting the careful
      gain staging and pushing the whole mix hotter than intended. Disabled,
      the limiter is a pure peak-safety catch for summed transients.
    """
    narr = f"[0:a]volume={narration_volume},{_AFMT}"
    music = f"[1:a]volume={music_volume_db}dB,{_AFMT}[music]"
    tail = ("amix=inputs=2:duration=first:dropout_transition=3:normalize=0,"
            "alimiter=level=disabled:limit=0.95,aresample=async=1:first_pts=0[a]")
    if duck:
        # Narration is the sidechain signal that ducks the music: when the
        # voice is present the bed dips a few dB and breathes back up in the
        # pauses — the standard radio/podcast/DaVinci auto-duck behaviour.
        return (
            f"{narr}[narr];"
            f"{music};"
            "[narr]asplit=2[narr_main][narr_sc];"
            f"[music][narr_sc]sidechaincompress=threshold={duck_threshold}"
            f":ratio={duck_ratio}:attack={duck_attack}:release={duck_release}"
            ":makeup=1[music_ducked];"
            f"[narr_main][music_ducked]{tail}"
        )
    return f"{narr}[narr];{music};[narr][music]{tail}"


def add_background_music(
    video_in: Path, video_out: Path, music_file: Path, music_volume_db: float, narration_volume: float, audio_bitrate: str,
    duck: bool = True, duck_ratio: float = 2.0, duck_attack: float = 20.0, duck_release: float = 350.0,
    duck_threshold: float = 0.08,
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

    filter_complex = build_mix_filter(
        narration_volume=narration_volume, music_volume_db=music_volume_db,
        duck=duck, duck_ratio=duck_ratio, duck_attack=duck_attack,
        duck_release=duck_release, duck_threshold=duck_threshold,
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
    runtime.run(cmd, check=True)
    return video_out


def resolve_default_input(output_root: Path, name: str) -> Path:
    found = find_latest_long_video(output_root, name)
    if found is None:
        raise FileNotFoundError(
            f"No joined long video found for '{name}' under {(output_root / name).resolve()}. "
            "Run the join step first."
        )
    return found


def narration_loudness_reference(measured_lufs: float | None, narration_volume: float) -> float | None:
    """Return voice loudness after the linear narration gain used by the mix."""
    if narration_volume <= 0:
        raise ValueError("--narration-volume must be positive.")
    if measured_lufs is None:
        return None
    return measured_lufs + 20.0 * math.log10(narration_volume)


def main() -> int:
    args = parse_args()
    if args.narration_volume <= 0:
        raise ValueError("--narration-volume must be positive.")
    name = project_name(args.project_root, args.project_name)
    video_in = (args.input or resolve_default_input(args.output_root, name)).resolve()
    if args.replace:
        video_out = video_in
    else:
        video_out = (args.output or default_bgm_output(video_in, args.music_volume_db)).resolve()

    requested_music = args.background_music or configured_background_music()
    if args.background_music is None:
        print(f"[bgm] using default background music: {requested_music}", flush=True)
    music = requested_music
    if not requested_music.is_file():
        raise FileNotFoundError(f"Background music not found: {requested_music}")
    if not args.raw_music:
        from mediaconductor.video_pipeline.audio_audit import ffprobe_duration
        from mediaconductor.video_pipeline.music_bed import (
            condition_bed,
            describe_report,
            prepare_music_bed,
        )

        video_duration = ffprobe_duration(video_in) or 0.0
        music, bed_report = prepare_music_bed(requested_music, video_duration, args.work_dir)
        print(describe_report(bed_report), flush=True)

        # Tame the bed's own dynamics (and carve the vocal band) so it sits at
        # a consistent level under the voice instead of swelling and receding
        # on its own. Measured *after* this, so the loudnorm offset below stays
        # a true LU separation of the conditioned bed.
        if args.condition_bed or args.eq_carve:
            music, cond_report = condition_bed(
                music, args.work_dir, compress=args.condition_bed, eq_carve=args.eq_carve,
            )
            if cond_report.get("conditioned"):
                print(f"[music-condition] compressed dynamics"
                      f"{' + carved 2-5 kHz vocal band' if args.eq_carve else ''} -> {music}",
                      flush=True)
            elif cond_report.get("note"):
                print(f"[music-condition] {cond_report['note']}", flush=True)

    # Align the music stem to the actual joined narration before the user's
    # offset. The narration gain is included in the reference; final whole-
    # mix normalization can then move both stems without changing separation.
    effective_volume_db = args.music_volume_db
    if not args.no_music_loudnorm:
        from mediaconductor.video_pipeline.music_bed import (
            MUSIC_LOUDNESS_REF,
            measure_integrated_lufs,
            music_loudnorm_pregain,
        )

        narration_measured = measure_integrated_lufs(video_in)
        narration_reference = narration_loudness_reference(narration_measured, args.narration_volume)
        music_measured = measure_integrated_lufs(music)
        reference = narration_reference if narration_reference is not None else MUSIC_LOUDNESS_REF
        pregain = music_loudnorm_pregain(music_measured, reference)
        if music_measured is None:
            print("[music-loudnorm] could not measure music loudness; applying the offset to the raw file", flush=True)
        else:
            effective_volume_db = args.music_volume_db + pregain
            if narration_reference is None:
                print(f"[music-loudnorm] could not measure narration; using fallback reference "
                      f"{MUSIC_LOUDNESS_REF:g} LUFS", flush=True)
            else:
                print(f"[music-loudnorm] narration measured {narration_measured:.1f} LUFS; "
                      f"after {args.narration_volume:g}x gain the reference is "
                      f"{narration_reference:.1f} LUFS", flush=True)
            print(f"[music-loudnorm] music measured {music_measured:.1f} LUFS; pre-gain {pregain:+.1f} dB "
                  f"to the {reference:g} LUFS reference -> effective volume "
                  f"{effective_volume_db:.1f} dB (a true {abs(args.music_volume_db):g} LU below narration)",
                  flush=True)

    add_background_music(
        video_in, video_out, music, effective_volume_db, args.narration_volume, args.audio_bitrate,
        duck=args.duck, duck_ratio=args.duck_ratio, duck_attack=args.duck_attack,
        duck_release=args.duck_release, duck_threshold=args.duck_threshold,
    )
    print(f"\nAdded background music: {video_out}", flush=True)
    emit_result(outputs=[video_out])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
