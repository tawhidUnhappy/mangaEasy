from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from mangaeasy.tools.external import python_command, resolve_tool_dir, tool_env


def print_help() -> None:
    print("usage: mangaeasy index-tts")
    print()
    print("Generate narration audio by delegating to the sibling ./index-tts uv environment.")
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

    command = [*python_command(tool_dir), str(script), *sys.argv[1:]]
    print(f"[tool:index-tts] {tool_dir}", flush=True)
    print(" ".join(command), flush=True)
    return subprocess.run(command, cwd=tool_dir, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
