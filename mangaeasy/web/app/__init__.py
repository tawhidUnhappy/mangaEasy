"""mangaeasy.web.app — the mangaEasy control center.

``mangaeasy app`` opens the Electron desktop GUI (see ``desktop/``), an
isolated, project-local Node.js + Electron app that talks to this Python
package only by spawning ``mangaeasy <command>`` subprocesses. Nothing here
imports Electron's JS/TS code, and Electron never imports this package --
they only meet at the CLI boundary.

The NiceGUI/pywebview GUI this replaced has been removed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _desktop_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "desktop"


def _electron_binary(desktop_dir: Path) -> Path | None:
    candidate = desktop_dir / "node_modules" / "electron" / "dist" / "electron.exe"
    if candidate.exists():
        return candidate
    candidate = desktop_dir / "node_modules" / "electron" / "dist" / "electron"
    return candidate if candidate.exists() else None


def main() -> int:
    desktop_dir = _desktop_dir()
    built_main = desktop_dir / "out" / "main" / "index.js"
    electron_bin = _electron_binary(desktop_dir)

    if not built_main.exists() or electron_bin is None:
        print(
            "[FATAL] The Electron desktop app isn't built yet.\n"
            f"        Expected: {built_main}\n"
            f"        Run, from {desktop_dir}:\n"
            "          npm install\n"
            "          npm run build",
            file=sys.stderr,
        )
        return 1

    env = dict(os.environ)
    # A parent shell (or an Electron-based IDE host) may have this set, which
    # makes any Electron binary it spawns run as plain Node -- `electron.app`
    # ends up undefined and the window never opens.
    env.pop("ELECTRON_RUN_AS_NODE", None)

    result = subprocess.run([str(electron_bin), "."], cwd=desktop_dir, env=env)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
