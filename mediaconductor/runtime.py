"""mediaconductor.runtime — how this process spawns every child process.

Two concerns live here, and every other module must route through them:

* **Re-invoking our own CLI.** Normal installs spawn subcommands as
  ``python -m mediaconductor.cli <command>``. In a frozen (PyInstaller) build there
  is no ``python`` and no ``-m`` — the executable *is* the CLI — so the prefix
  collapses to the exe itself. Build such argv via :func:`cli_command`.

* **Spawning anything at all** (ffmpeg, uv, git, external tool envs). On
  Windows, a child console process spawned from a parent that has no visible
  console — a detached background job, an MCP server started by an editor, a
  GUI-subsystem host — gets a brand-new *visible* console, which the user sees
  as a blank terminal window popping up. :func:`run` and :func:`popen` are
  drop-in ``subprocess.run``/``subprocess.Popen`` replacements that always
  apply CREATE_NO_WINDOW there; on Linux/macOS they add nothing. Raw
  ``subprocess`` calls are forbidden outside this module (enforced by
  tests/test_repository_hygiene.py).
"""

from __future__ import annotations

import subprocess
import sys


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def cli_command(command: str, *args: str) -> list[str]:
    """argv that runs ``mediaconductor <command> [args...]`` in a fresh process."""
    if is_frozen():
        return [sys.executable, command, *args]
    return [sys.executable, "-m", "mediaconductor.cli", command, *args]


def popen_kwargs() -> dict:
    """Extra kwargs for subprocess.Popen/run that suppress console windows on Windows.

    Prefer :func:`run`/:func:`popen`, which apply this automatically; this
    stays public for call sites that must assemble kwargs themselves.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _windowless(kwargs: dict) -> dict:
    """Merge CREATE_NO_WINDOW into caller kwargs without clobbering their flags.

    DETACHED_PROCESS and CREATE_NEW_CONSOLE each dictate their own console
    disposition; Windows ignores (or rejects) CREATE_NO_WINDOW combined with
    them, so an explicit choice by the caller is left untouched.

    Caution for callers choosing DETACHED_PROCESS: never use it to spawn a
    venv `python.exe` or console-script shim — those launchers respawn the
    real binary as a child, and that console-less child allocates a brand-new
    console that Windows 11 shows as a visible blank terminal. Detach with
    CREATE_NO_WINDOW (own hidden console) instead; see jobs.py.
    """
    if sys.platform != "win32":
        return kwargs
    flags = kwargs.get("creationflags", 0)
    explicit = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    if flags & explicit:
        return kwargs
    return {**kwargs, "creationflags": flags | subprocess.CREATE_NO_WINDOW}


def run(argv, **kwargs) -> subprocess.CompletedProcess:
    """``subprocess.run`` that never pops a console window on Windows."""
    return subprocess.run(argv, **_windowless(kwargs))


def popen(argv, **kwargs) -> subprocess.Popen:
    """``subprocess.Popen`` that never pops a console window on Windows."""
    return subprocess.Popen(argv, **_windowless(kwargs))
