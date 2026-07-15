"""Run pinned ACE-Step 1.5 song generation in its isolated uv project."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from mangaeasy.brand import CLI_NAME
from mangaeasy.runtime import popen_kwargs
from mangaeasy.tools.external import python_command, resolve_tool_dir, tool_env
from mangaeasy.utils import emit_result


def main() -> int:
    parser = argparse.ArgumentParser(prog=f"{CLI_NAME} ace-step")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--lyrics-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260715)
    parser.add_argument("--duration", type=float, default=-1.0)
    parser.add_argument("--language", default="en")
    parser.add_argument("--bpm", type=int)
    parser.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="auto")
    args = parser.parse_args()
    tool_dir = resolve_tool_dir("ace-step", required=False)
    if tool_dir is None:
        print(f"[error] ACE-Step is not installed. Run: {CLI_NAME} install-tool ace-step")
        return 1
    adapter = tool_dir / "generate_ace_step.py"
    if not adapter.is_file():
        print(f"[error] ACE-Step adapter missing. Re-run: {CLI_NAME} install-tool ace-step --update")
        return 1
    command = [
        *python_command(tool_dir), str(adapter), "--prompt", args.prompt,
        "--lyrics-file", str(args.lyrics_file.resolve()), "--output", str(args.output.resolve()),
        "--seed", str(args.seed), "--duration", str(args.duration),
        "--language", args.language, "--device", args.device,
    ]
    if args.bpm is not None:
        command += ["--bpm", str(args.bpm)]
    env = tool_env()
    env["PYTHONUNBUFFERED"] = "1"
    rc = subprocess.run(command, cwd=tool_dir, env=env, **popen_kwargs()).returncode
    if rc or not args.output.is_file():
        return rc or 1
    emit_result(outputs=[args.output.resolve()], seed=args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
