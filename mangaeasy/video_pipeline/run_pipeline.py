from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from mangaeasy.brand import CLI_NAME
from mangaeasy.defaults import (
    DEFAULT_NARRATION_VOLUME,
    default_background_music,
    default_manga_video_audio_fade_ms,
    default_manga_video_audio_source,
    default_music_volume_db,
    default_tts_engine,
)
from mangaeasy.runtime import cli_command
from mangaeasy.tools.hardware import has_nvidia_gpu
from mangaeasy.utils import emit_result
from mangaeasy.video_pipeline.common import (
    DEFAULT_AUDIO_ROOT,
    DEFAULT_KOKORO_ROOT,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PROJECT_ROOT,
    DEFAULT_WORK_DIR,
    clamp_gpu_workers,
    find_latest_long_video,
    merge_item_selection,
    project_name,
)


def resolve_tts_engine(choice: str, speaker_wav: Path | None) -> str:
    """Pick the TTS engine. explicit choices are honored; auto can fall back.

    IndexTTS gives the best quality but needs an NVIDIA GPU, an installed
    index-tts tool env with checkpoints, and a speaker reference WAV. If any
    piece is missing, auto falls back to Kokoro (light, runs well on CPU).
    """
    if choice in ("kokoro", "indextts"):
        return choice

    from mangaeasy.tools.external import resolve_tool_dir
    from mangaeasy.video_pipeline.generate_audio_indextts import _default_speaker_wav

    if not has_nvidia_gpu():
        print("[tts:auto] no NVIDIA GPU -> Kokoro")
        return "kokoro"
    tool_dir = resolve_tool_dir("index-tts", required=False)
    if tool_dir is None:
        print("[tts:auto] GPU found, but index-tts is not installed -> Kokoro")
        print(f"           (get the higher-quality TTS with: {CLI_NAME} install-tool index-tts)")
        return "kokoro"
    if not (tool_dir / "checkpoints" / "config.yaml").exists():
        print("[tts:auto] index-tts found, but model checkpoints are missing -> Kokoro")
        print(f"           (re-run: {CLI_NAME} install-tool index-tts)")
        return "kokoro"
    ref = (speaker_wav or _default_speaker_wav()).resolve()
    if not ref.is_file():
        print("[tts:auto] GPU + IndexTTS ready, but no speaker reference WAV -> Kokoro")
        print(f"           (expected {ref}; set config.system.json -> tts.speaker_wav or pass --speaker-wav)")
        return "kokoro"
    print(f"[tts:auto] NVIDIA GPU + IndexTTS installed -> IndexTTS (speaker: {ref.name})")
    return "indextts"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate narration audio (IndexTTS when your machine is ready for it, Kokoro otherwise), "
                     "then build videos."
    )
    parser.add_argument("--tts", choices=("auto", "kokoro", "indextts"), default=default_tts_engine(),
                        help="TTS engine. Defaults to config.system.json -> tts.engine, else auto. "
                             "auto picks IndexTTS when an NVIDIA GPU, the index-tts "
                             "tool + checkpoints, and a speaker WAV are all present, else Kokoro -- this is what "
                             f"keeps '{CLI_NAME} video' working on any machine out of the box. Force one "
                             "explicitly with --tts indextts / --tts kokoro; forcing indextts on a machine "
                             "without it installed fails outright instead of falling back to Kokoro.")
    parser.add_argument("--speaker-wav", type=Path, default=None,
                        help="IndexTTS speaker reference WAV (defaults to config.system.json -> tts.speaker_wav).")
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
    parser.add_argument("--resume-audio", action="store_true",
                        help="Delete the most recently generated audio file plus the previous 5 before "
                             "generating, in case the last run was interrupted mid-write.")
    parser.add_argument("--skip-audio", action="store_true",
                        help="Skip narration audio generation entirely and reuse whatever audio "
                             "already exists on disk; just re-render and re-join the video.")
    parser.add_argument("--audio-source", choices=("raw", "faded"), default=default_manga_video_audio_source(),
                        help="Which audio render/join read. 'raw' is the straight TTS output. "
                             "'faded' adds tiny fade-in/out copies (removes edge clicks/pops) written "
                             "to a sibling folder; the raw audio is never deleted. Defaults to "
                             "config.system.json -> manga_video.audio_source, else faded.")
    parser.add_argument("--audio-fade-ms", type=float, default=default_manga_video_audio_fade_ms(),
                        help="Symmetric fade at both edges of each narration clip when --audio-source=faded "
                             "(config: manga_video.audio_fade_ms; default 8 ms).")
    parser.add_argument("--voice", default="af_heart")
    parser.add_argument("--lang", default="a")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="auto")
    parser.add_argument("--emo-alpha", type=float, default=None,
                        help="IndexTTS only: strength of per-entry narration 'emotion' fields "
                             "(default 0.6; 0 disables). Kokoro ignores emotion fields.")
    parser.add_argument("--no-emotion", action="store_true",
                        help="IndexTTS only: ignore narration 'emotion' fields.")
    parser.add_argument("--build-long-video", action="store_true")
    parser.add_argument("--allow-gaps", action="store_true",
                        help="When joining the long video, skip chapters that are genuinely missing "
                             "(e.g. a scanlation gap) instead of failing. Off by default.")
    parser.add_argument("--normalize-audio", action="store_true",
                        help="After the long video is built, loudness-normalize it in place "
                             "for YouTube (-14 LUFS integrated, two-pass).")
    parser.add_argument("--background-style", choices=("blur", "black", "image"), default="blur")
    parser.add_argument("--background-image", type=Path, default=None)
    parser.add_argument("--blur-sigma", type=float, default=28.0)
    parser.add_argument("--blur-downscale", type=int, default=4)
    parser.add_argument("--blur-backend", choices=("auto", "vulkan", "cpu"), default="auto")
    parser.add_argument("--background-brightness", type=float, default=-0.06)
    parser.add_argument("--background-saturation", type=float, default=1.08)
    parser.add_argument("--background-music", type=Path, default=None,
                        help="Music file for the final long-video mix. Defaults to config.system.json -> bgm.file "
                             "or the tracked default music asset when present; use --no-background-music to skip.")
    parser.add_argument("--no-background-music", action="store_true",
                        help="Do not add background music even if a default BGM file is configured.")
    parser.add_argument("--music-volume-db", type=float, default=default_music_volume_db(),
                        help="How far the music sits below the narration, in dB (negative = quieter). The music "
                             "stem is loudness-aligned to the actual joined narration first, so this is "
                             "a true LU separation; default -26 suits dense recap narration, -20 to -22 sparser "
                             "voiceover.")
    parser.add_argument("--no-music-loudnorm", action="store_true",
                        help="Apply --music-volume-db without aligning music loudness to the narration first.")
    parser.add_argument("--condition-bed", action=argparse.BooleanOptionalAction, default=True,
                        help="Compress the music's dynamic range so it sits at a consistent level under the "
                             "narration (on by default; --no-condition-bed applies only the flat dB offset).")
    parser.add_argument("--eq-carve", action=argparse.BooleanOptionalAction, default=True,
                        help="Dip the music in the 2-5 kHz vocal band so it masks the voice less (on by default).")
    parser.add_argument("--narration-volume", type=float, default=DEFAULT_NARRATION_VOLUME)
    parser.add_argument("--duck", action=argparse.BooleanOptionalAction, default=True,
                        help="Sidechain-duck the music under the narration so it dips when the voice is present "
                             "and breathes back up in the pauses (on by default; --no-duck holds it flat).")
    parser.add_argument("--duck-ratio", type=float, default=2.0)
    parser.add_argument("--duck-attack", type=float, default=20.0)
    parser.add_argument("--duck-release", type=float, default=350.0)
    parser.add_argument("--duck-threshold", type=float, default=0.08)
    parser.add_argument("--audio-bitrate", default="128k")
    parser.add_argument("--render-mode", choices=("segments", "concat-images"), default="segments")
    parser.add_argument("--encoder", default="auto")
    parser.add_argument("--video-preset", default="p1")
    parser.add_argument("--cq", type=int, default=18)
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--video-workers", type=int, default=3,
                         help="Item folders to render in parallel. NVENC consumer GPUs typically "
                              "cap at ~3 concurrent encode sessions, so going much higher than "
                              "that won't add throughput.")
    parser.add_argument("--gpu-workers", type=int, default=1,
                         help="Run this many TTS worker processes in parallel during audio "
                              "generation, each loading its own model copy. Multiplies VRAM use "
                              "by this count -- only raise it if the GPU has headroom.")
    parser.add_argument("--respect-claims", action="store_true",
                        help="Abort (exit 1) if another live agent's workboard claim covers any "
                             "selected item at this stage (see docs/multi-agent.md).")
    parser.add_argument("--agent", default=None,
                        help="This agent's identity for --respect-claims "
                             "(default: $MANGAEASY_AGENT or user@host).")
    return parser.parse_args()


def run(command: list[str], cwd: Path) -> None:
    print(f"\n[{cwd}] {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    args = parse_args()
    if args.audio_fade_ms <= 0:
        raise ValueError("--audio-fade-ms must be positive.")
    args.gpu_workers = clamp_gpu_workers(args.gpu_workers)
    if args.respect_claims:
        from mangaeasy.workboard import respect_claims_gate

        if not respect_claims_gate(args.project_root, args.items, args.item_range, ("audio", "render",), args.agent):
            return 1
    cwd = Path.cwd()
    selected_items = merge_item_selection(args.items, args.item_range)
    background_music = None if args.no_background_music else (args.background_music or default_background_music())
    if args.build_long_video and background_music is None and not args.no_background_music:
        print("[bgm] no configured/default background music found; keeping the long video narration-only.", flush=True)
    elif args.build_long_video and args.background_music is None and background_music is not None:
        print(f"[bgm] using default background music: {background_music}", flush=True)

    engine = resolve_tts_engine(args.tts, args.speaker_wav)
    if engine == "indextts":
        audio_cmd = cli_command(
            "video-audio-indextts",
            "--project-root", str(args.project_root),
            "--audio-root", str(args.audio_root),
        )
        if args.speaker_wav is not None:
            audio_cmd += ["--speaker-wav", str(args.speaker_wav)]
        if args.emo_alpha is not None:
            audio_cmd += ["--emo-alpha", str(args.emo_alpha)]
        if args.no_emotion:
            audio_cmd.append("--no-emotion")
    else:
        audio_cmd = cli_command(
            "video-audio",
            "--project-root", str(args.project_root),
            "--audio-root", str(args.audio_root),
            "--work-dir", str(args.work_dir),
            "--kokoro-root", str(args.kokoro_root),
            "--voice", args.voice,
            "--lang", args.lang,
            "--speed", str(args.speed),
            "--device", args.device,
        )
    if args.project_name:
        audio_cmd += ["--project-name", args.project_name]
    if args.overwrite_audio:
        audio_cmd.append("--overwrite")
    if args.resume_audio:
        audio_cmd.append("--resume")
    if args.gpu_workers != 1:
        audio_cmd += ["--gpu-workers", str(args.gpu_workers)]
    if selected_items:
        audio_cmd += ["--items", *selected_items]

    effective_audio_root = args.audio_root.resolve()
    fade_cmd: list[str] | None = None
    if args.audio_source == "faded":
        effective_audio_root = effective_audio_root.with_name(effective_audio_root.name + "_faded")
        fade_cmd = cli_command(
            "video-fade-audio",
            "--project-root", str(args.project_root),
            "--source-audio-root", str(args.audio_root),
            "--output-audio-root", str(effective_audio_root),
            "--fade-ms", str(args.audio_fade_ms),
            "--overwrite",
        )
        if args.project_name:
            fade_cmd += ["--project-name", args.project_name]
        if selected_items:
            fade_cmd += ["--items", *selected_items]

    video_cmd = cli_command(
        "video-render",
        "--project-root", str(args.project_root),
        "--audio-root", str(effective_audio_root),
        "--output-root", str(args.output_root),
        "--work-dir", str(args.work_dir),
        "--background-style", args.background_style,
        "--blur-sigma", str(args.blur_sigma),
        "--blur-downscale", str(args.blur_downscale),
        "--blur-backend", args.blur_backend,
        "--background-brightness", str(args.background_brightness),
        "--background-saturation", str(args.background_saturation),
        "--audio-bitrate", args.audio_bitrate,
        "--render-mode", args.render_mode,
        "--encoder", args.encoder,
        "--preset", args.video_preset,
        "--cq", str(args.cq),
        "--fps", str(args.fps),
        "--workers", str(args.video_workers),
    )
    if args.project_name:
        video_cmd += ["--project-name", args.project_name]
    if args.background_image is not None:
        video_cmd += ["--background-image", str(args.background_image)]
    if args.overwrite_video or args.skip_audio:
        video_cmd.append("--overwrite")
    if selected_items:
        video_cmd += ["--items", *selected_items]

    if args.skip_audio:
        print("\n[skip-audio] Reusing existing narration audio; not regenerating it.", flush=True)
    else:
        run(audio_cmd, cwd)
    if fade_cmd is not None:
        run(fade_cmd, cwd)
    run(video_cmd, cwd)
    if args.build_long_video:
        name = project_name(args.project_root, args.project_name)
        # Join narration first. If music is requested it is mixed next; one
        # final normalization pass then targets the complete deliverable.
        # Normalizing before BGM is incorrect because adding a stem changes
        # both integrated loudness and true peak.
        long_cmd = cli_command(
            "video-join",
            "--project-root", str(args.project_root),
            "--output-root", str(args.output_root),
            "--work-dir", str(args.work_dir),
            "--narration-dir", str(effective_audio_root / name / "_items"),
            "--audio-root", str(effective_audio_root),
            "--audio-bitrate", args.audio_bitrate,
            # When BGM follows, that stage owns the configured voice lift.
            # Otherwise the narration-only join owns it. Never apply it twice.
            "--narration-volume", str(1.0 if background_music is not None else args.narration_volume),
            "--overwrite",
        )
        if args.project_name:
            long_cmd += ["--project-name", args.project_name]
        if selected_items:
            long_cmd += ["--items", *selected_items]
        if args.allow_gaps:
            long_cmd.append("--allow-gaps")
        run(long_cmd, cwd)

        long_video = find_latest_long_video(args.output_root, name)
        if long_video is None:
            raise FileNotFoundError(f"Join completed but no long video was found for '{name}'.")

        if background_music is not None:
            bgm_cmd = cli_command(
                "video-add-bgm",
                "--project-root", str(args.project_root),
                "--output-root", str(args.output_root),
                "--input", str(long_video),
                "--background-music", str(background_music),
                "--music-volume-db", str(args.music_volume_db),
                "--narration-volume", str(args.narration_volume),
                "--audio-bitrate", args.audio_bitrate,
                "--work-dir", str(args.work_dir),
                # Keep the exact timestamped join path stable for anything
                # watching the full pipeline, so opt into in-place
                # replacement here. Trying several mixes without overwriting
                # one another is the standalone video-add-bgm default.
                "--replace",
            )
            if args.no_music_loudnorm:
                bgm_cmd += ["--no-music-loudnorm"]
            bgm_cmd += ["--condition-bed" if args.condition_bed else "--no-condition-bed"]
            bgm_cmd += ["--eq-carve" if args.eq_carve else "--no-eq-carve"]
            if args.duck:
                bgm_cmd += ["--duck", "--duck-ratio", str(args.duck_ratio),
                            "--duck-attack", str(args.duck_attack), "--duck-release", str(args.duck_release),
                            "--duck-threshold", str(args.duck_threshold)]
            else:
                bgm_cmd += ["--no-duck"]
            if args.project_name:
                bgm_cmd += ["--project-name", args.project_name]
            run(bgm_cmd, cwd)

        if args.normalize_audio:
            norm_cmd = cli_command(
                "video-normalize-audio",
                "--project-root", str(args.project_root),
                "--output-root", str(args.output_root),
                "--input", str(long_video),
                "--audio-bitrate", args.audio_bitrate,
                "--replace",
            )
            if args.project_name:
                norm_cmd += ["--project-name", args.project_name]
            run(norm_cmd, cwd)

    # Machine-parsable summary of what this run produced: the sub-commands
    # each emit their own MANGAEASY_RESULT, but agents driving the all-in-one
    # `video` command shouldn't have to scrape a child's output out of ours.
    name = project_name(args.project_root, args.project_name)
    outputs: list[str] = []
    if args.build_long_video:
        latest_long = find_latest_long_video(args.output_root, name)
        if latest_long is not None:
            outputs = [str(latest_long)]
    if not outputs:
        items_dir = args.output_root.resolve() / name / "items"
        outputs = sorted(str(p) for p in items_dir.glob("item_*.mp4")) if items_dir.exists() else []
    emit_result(outputs=outputs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
