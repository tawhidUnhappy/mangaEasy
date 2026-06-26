from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from mangaeasy.runtime import cli_command
from mangaeasy.video_pipeline.common import (
    DEFAULT_AUDIO_ROOT,
    DEFAULT_KOKORO_ROOT,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PROJECT_ROOT,
    DEFAULT_WORK_DIR,
    merge_item_selection,
    project_name,
)


def resolve_tts_engine(choice: str, speaker_wav: Path | None) -> str:
    """Pick the TTS engine. auto = IndexTTS on GPU machines, Kokoro otherwise.

    IndexTTS gives the best quality but needs an NVIDIA GPU, an installed
    index-tts tool env with checkpoints, and a speaker reference WAV. If any
    piece is missing, auto falls back to Kokoro (light, runs well on CPU).
    """
    if choice in ("kokoro", "indextts"):
        return choice

    from mangaeasy.tools.external import resolve_tool_dir
    from mangaeasy.video_pipeline.generate_audio_indextts import _default_speaker_wav

    if shutil.which("nvidia-smi") is None:
        print("[tts:auto] no NVIDIA GPU -> Kokoro")
        return "kokoro"
    tool_dir = resolve_tool_dir("index-tts", required=False)
    if tool_dir is None:
        print("[tts:auto] GPU found, but index-tts is not installed -> Kokoro")
        print("           (get the higher-quality TTS with: mangaeasy install-tool index-tts)")
        return "kokoro"
    if not (tool_dir / "checkpoints" / "config.yaml").exists():
        print("[tts:auto] index-tts found, but model checkpoints are missing -> Kokoro")
        print("           (re-run: mangaeasy install-tool index-tts)")
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
        description="Generate narration audio (IndexTTS on GPU machines, Kokoro otherwise), then build videos."
    )
    parser.add_argument("--tts", choices=("auto", "kokoro", "indextts"), default="auto",
                        help="TTS engine. auto picks IndexTTS when a GPU and the tool are available, else Kokoro.")
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
    parser.add_argument("--archive-audio", action="store_true",
                        help="Audio is expensive to regenerate, so instead of deleting/overwriting any "
                             "existing file (via --overwrite-audio or --resume-audio), move it into "
                             "<audio-root>/<project>/old/run_NNNN/ first.")
    parser.add_argument("--audio-source", choices=("raw", "faded"), default="raw",
                        help="Which audio render/join read. 'raw' is the straight TTS output. "
                             "'faded' adds tiny fade-in/out copies (removes edge clicks/pops) written "
                             "to a sibling folder; the raw audio is never deleted.")
    parser.add_argument("--voice", default="af_heart")
    parser.add_argument("--lang", default="a")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="auto")
    parser.add_argument("--build-long-video", action="store_true")
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
    parser.add_argument("--background-music", type=Path, default=None)
    parser.add_argument("--music-volume", type=float, default=0.035)
    parser.add_argument("--narration-volume", type=float, default=1.0)
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
    return parser.parse_args()


def run(command: list[str], cwd: Path) -> None:
    print(f"\n[{cwd}] {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    args = parse_args()
    cwd = Path.cwd()
    selected_items = merge_item_selection(args.items, args.item_range)

    engine = resolve_tts_engine(args.tts, args.speaker_wav)
    if engine == "indextts":
        audio_cmd = cli_command(
            "video-audio-indextts",
            "--project-root", str(args.project_root),
            "--audio-root", str(args.audio_root),
        )
        if args.speaker_wav is not None:
            audio_cmd += ["--speaker-wav", str(args.speaker_wav)]
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
    if args.archive_audio:
        audio_cmd.append("--archive-audio")
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
        long_cmd = cli_command(
            "video-join",
            "--project-root", str(args.project_root),
            "--output-root", str(args.output_root),
            "--work-dir", str(args.work_dir),
            "--narration-dir", str(effective_audio_root / name / "_items"),
            "--audio-root", str(effective_audio_root),
            "--audio-bitrate", args.audio_bitrate,
            "--overwrite",
        )
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

        if args.normalize_audio:
            norm_cmd = cli_command(
                "video-normalize-audio",
                "--project-root", str(args.project_root),
                "--output-root", str(args.output_root),
                "--replace",
            )
            if args.project_name:
                norm_cmd += ["--project-name", args.project_name]
            run(norm_cmd, cwd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
