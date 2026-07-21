from __future__ import annotations

import argparse

from mediaconductor import runtime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mediaconductor.defaults import default_speaker_wav
from mediaconductor.tools.external import python_command, resolve_tool_dir, tool_env
from mediaconductor.video_pipeline.common import (
    DEFAULT_AUDIO_ROOT,
    DEFAULT_PROJECT_ROOT,
    clamp_gpu_workers,
    item_dirs,
    merge_item_selection,
)
from mediaconductor.video_pipeline.item_assets import load_narration, validate_calm_narration


def _default_speaker_wav() -> Path:
    return default_speaker_wav()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate narration audio via IndexTTS2.")
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--items", nargs="*")
    parser.add_argument("--item-range")
    parser.add_argument("--speaker-wav", type=Path, default=None,
                        help="Speaker reference WAV. Defaults to config.system.json → tts.speaker_wav.")
    parser.add_argument("--index-tts-root", type=Path, default=None,
                        help="Path to index-tts directory. Auto-detected if omitted.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--resume", action="store_true",
                        help="Delete the most recently generated audio file plus the previous 5 before "
                             "generating, in case the last run was interrupted mid-write.")
    parser.add_argument("--emo-alpha", type=float, default=None,
                        help="Strength of per-entry 'emotion' fields in narration.json (IndexTTS2 "
                             "emo_text blending; default 0.6, 0 disables).")
    parser.add_argument("--no-emotion", action="store_true",
                        help="Synthesize in a plain neutral delivery. The calm-policy preflight "
                             "still rejects invalid emotion fields.")
    parser.add_argument("--gpu-workers", type=int, default=1,
                         help="Run this many IndexTTS2 worker processes in parallel, each loading "
                              "its own model copy and handling a separate slice of item folders. "
                              "Multiplies VRAM use by this count -- only raise it if the GPU has "
                              "headroom. With --resume, each worker only re-verifies its own slice's "
                              "last 5 files, not a single global sequence.")
    return parser.parse_args()


def shard_item_names(names: list[str], shards: int) -> list[list[str]]:
    if shards <= 1 or len(names) <= 1:
        return [names]
    size = -(-len(names) // shards)  # ceil division
    return [names[i:i + size] for i in range(0, len(names), size)]


def main() -> int:
    args = parse_args()
    args.gpu_workers = clamp_gpu_workers(args.gpu_workers)
    speaker_wav = (args.speaker_wav or _default_speaker_wav()).resolve()
    project_root = args.project_root.resolve()

    selected = item_dirs(project_root, merge_item_selection(args.items, args.item_range))
    if not selected:
        print(f"[FATAL] No item folders found in {project_root}")
        return 1
    # Validate the complete selection before any worker can archive audio,
    # load a model, or generate a shard.
    for item_dir in selected:
        narration = load_narration(item_dir)
        validate_calm_narration(narration, item_dir)

    tool_dir = (
        args.index_tts_root.resolve()
        if args.index_tts_root
        else resolve_tool_dir("index-tts")
    )
    if tool_dir is None:
        print("[FATAL] Could not locate index-tts directory.")
        return 1

    script = Path(__file__).resolve().parent.parent / "audio" / "tts_pipeline.py"
    env = tool_env()
    env["INDEX_TTS_ROOT"] = str(tool_dir)
    env["INDEX_TTS_DIR"] = str(tool_dir)

    def base_cmd() -> list[str]:
        cmd = [
            *python_command(tool_dir),
            str(script),
            "--project-root", str(project_root),
            "--audio-root", str(args.audio_root.resolve()),
            "--speaker-wav", str(speaker_wav),
        ]
        if args.project_name:
            cmd += ["--project-name", args.project_name]
        if args.overwrite:
            cmd.append("--overwrite")
        if args.resume:
            cmd.append("--resume")
        if args.emo_alpha is not None:
            cmd += ["--emo-alpha", str(args.emo_alpha)]
        if args.no_emotion:
            cmd.append("--no-emotion")
        return cmd

    print(f"[tool:index-tts] {tool_dir}", flush=True)

    if args.gpu_workers <= 1:
        cmd = base_cmd()
        if args.items:
            cmd += ["--items", *args.items]
        if args.item_range:
            cmd += ["--item-range", args.item_range]
        result = runtime.run(cmd, cwd=tool_dir, env=env)
        return result.returncode

    shards = shard_item_names([item.name for item in selected], args.gpu_workers)
    if len(shards) == 1:
        cmd = base_cmd() + ["--items", *shards[0]]
        result = runtime.run(cmd, cwd=tool_dir, env=env)
        return result.returncode

    print(f"Sharding {len(selected)} item folder(s) across {len(shards)} IndexTTS worker process(es).",
          flush=True)
    commands = [base_cmd() + ["--items", *shard] for shard in shards]
    with ThreadPoolExecutor(max_workers=len(commands)) as executor:
        results = list(executor.map(lambda cmd: runtime.run(cmd, cwd=tool_dir, env=env), commands))
    return next((r.returncode for r in results if r.returncode != 0), 0)


if __name__ == "__main__":
    raise SystemExit(main())
