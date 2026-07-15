"""Separate vocals with the pinned, local HTDemucs-ft snapshot."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from mangaeasy.brand import CLI_NAME
from mangaeasy.runtime import popen_kwargs
from mangaeasy.tools.external import python_command, resolve_tool_dir, tool_env
from mangaeasy.utils import emit_result


def main() -> int:
    parser = argparse.ArgumentParser(prog=f"{CLI_NAME} demucs")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    args = parser.parse_args()
    tool_dir = resolve_tool_dir("demucs", required=False)
    if tool_dir is None:
        print(f"[error] Demucs is not installed. Run: {CLI_NAME} install-tool demucs")
        return 1
    audio = args.audio.resolve()
    if not audio.is_file():
        print(f"[error] audio file not found: {audio}")
        return 1
    output_dir = args.output_dir.resolve()
    scratch = output_dir / ".demucs"
    adapter = tool_dir / "separate_demucs.py"
    model_dir = tool_dir / "models" / "htdemucs-ft"
    if not adapter.is_file() or not (model_dir / "htdemucs_ft.yaml").is_file():
        print(
            "[error] Demucs is incomplete: the offline adapter or pinned model snapshot "
            f"is missing under {tool_dir}. Re-run: {CLI_NAME} install-tool demucs"
        )
        return 1
    command = [
        *python_command(tool_dir), str(adapter),
        "--audio", str(audio),
        "--output-dir", str(scratch),
        "--model-dir", str(model_dir),
        # The adapter runs inside Demucs' isolated environment and must decide
        # from that environment's Torch build whether CUDA is actually usable.
        "--device", args.device,
        "--segment", "7",
    ]
    env = tool_env()
    env["HF_HUB_OFFLINE"] = "1"
    rc = subprocess.run(command, cwd=tool_dir, env=env, **popen_kwargs()).returncode
    if rc:
        return rc
    matches = list(scratch.rglob(f"{audio.stem}/vocals.wav"))
    accompaniments = list(scratch.rglob(f"{audio.stem}/no_vocals.wav"))
    if not matches or not accompaniments:
        print(f"[error] Demucs completed without expected stems under: {scratch}")
        return 1
    output_dir.mkdir(parents=True, exist_ok=True)
    vocals, accompaniment = output_dir / "vocals.wav", output_dir / "accompaniment.wav"
    shutil.copy2(matches[0], vocals)
    shutil.copy2(accompaniments[0], accompaniment)
    emit_result(outputs=[vocals, accompaniment], model="local HTDemucs-ft", offline=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
