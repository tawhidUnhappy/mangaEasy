from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from mangaeasy.config import load_system_config
from mangaeasy.tools.external import python_command, resolve_tool_dir, tool_env
from mangaeasy.video_pipeline.common import DEFAULT_AUDIO_ROOT, DEFAULT_PROJECT_ROOT


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
    return parser.parse_args()


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

    cmd = [
        *python_command(tool_dir),
        str(script),
        "--project-root", str(args.project_root.resolve()),
        "--audio-root", str(args.audio_root.resolve()),
        "--speaker-wav", str(speaker_wav),
    ]
    if args.project_name:
        cmd += ["--project-name", args.project_name]
    if args.items:
        cmd += ["--items", *args.items]
    if args.item_range:
        cmd += ["--item-range", args.item_range]
    if args.overwrite:
        cmd.append("--overwrite")

    print(f"[tool:index-tts] {tool_dir}", flush=True)
    result = subprocess.run(cmd, cwd=tool_dir, env=env)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
