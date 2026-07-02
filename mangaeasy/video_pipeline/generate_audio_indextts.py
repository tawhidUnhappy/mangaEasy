from __future__ import annotations

import argparse
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mangaeasy.config import load_system_config
from mangaeasy.tools.external import python_command, resolve_tool_dir, tool_env
from mangaeasy.video_pipeline.common import (
    DEFAULT_AUDIO_ROOT,
    DEFAULT_PROJECT_ROOT,
    item_dirs,
    merge_item_selection,
)


def _default_speaker_wav() -> Path:
    cfg = load_system_config().get("tts", {})
    return Path(cfg.get("speaker_wav", "vocal/manga[vocal2].wav"))


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
    speaker_wav = (args.speaker_wav or _default_speaker_wav()).resolve()

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
            "--project-root", str(args.project_root.resolve()),
            "--audio-root", str(args.audio_root.resolve()),
            "--speaker-wav", str(speaker_wav),
        ]
        if args.project_name:
            cmd += ["--project-name", args.project_name]
        if args.overwrite:
            cmd.append("--overwrite")
        if args.resume:
            cmd.append("--resume")
        return cmd

    print(f"[tool:index-tts] {tool_dir}", flush=True)

    if args.gpu_workers <= 1:
        cmd = base_cmd()
        if args.items:
            cmd += ["--items", *args.items]
        if args.item_range:
            cmd += ["--item-range", args.item_range]
        result = subprocess.run(cmd, cwd=tool_dir, env=env)
        return result.returncode

    selected = item_dirs(args.project_root.resolve(), merge_item_selection(args.items, args.item_range))
    shards = shard_item_names([item.name for item in selected], args.gpu_workers)
    if len(shards) == 1:
        cmd = base_cmd() + ["--items", *shards[0]]
        result = subprocess.run(cmd, cwd=tool_dir, env=env)
        return result.returncode

    print(f"Sharding {len(selected)} item folder(s) across {len(shards)} IndexTTS worker process(es).",
          flush=True)
    commands = [base_cmd() + ["--items", *shard] for shard in shards]
    with ThreadPoolExecutor(max_workers=len(commands)) as executor:
        results = list(executor.map(lambda cmd: subprocess.run(cmd, cwd=tool_dir, env=env), commands))
    return next((r.returncode for r in results if r.returncode != 0), 0)


if __name__ == "__main__":
    raise SystemExit(main())
