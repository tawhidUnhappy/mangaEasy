from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from mangaeasy.tools.external import python_command, resolve_tool_dir, tool_env
from mangaeasy.runtime import popen_kwargs


def print_help() -> None:
    print("usage: mangaeasy index-tts")
    print()
    print("Generate narration audio by delegating to the managed index-tts uv environment.")
    print("The command reads mangaEasy config.json/config.system.json from the current project root.")
    print()
    print("Environment overrides:")
    print("  INDEX_TTS_ROOT or INDEX_TTS_DIR")
    print("  MANGAEASY_PROJECT_ROOT")


def main() -> int:
    if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
        print_help()
        return 0

    tool_dir = resolve_tool_dir("index-tts")
    assert tool_dir is not None

    script = Path(__file__).resolve().parents[1] / "audio" / "tts.py"
    env = tool_env()
    env.setdefault("MANGAEASY_PROJECT_ROOT", str(Path.cwd().resolve()))
    env.setdefault("INDEX_TTS_ROOT", str(tool_dir))
    env.setdefault("INDEX_TTS_DIR", str(tool_dir))
    # Force unbuffered output and UTF-8 so every print() line appears immediately
    # in the parent's log stream rather than arriving in one big flush at exit.
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    command = [*python_command(tool_dir), str(script), *sys.argv[1:]]
    print(f"[tool:index-tts] {tool_dir}", flush=True)
    print(" ".join(command), flush=True)
    # stderr=subprocess.STDOUT merges the grandchild's stderr into the same
    # stream as stdout so torchaudio/CUDA diagnostic messages reach the app log.
    return subprocess.run(
        command, cwd=tool_dir, env=env,
        stderr=subprocess.STDOUT,
        **popen_kwargs(),
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
