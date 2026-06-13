"""PyInstaller entry point for the standalone mangaEasy executable.

The frozen exe *is* the CLI: ``mangaEasy.exe <command> [args...]`` behaves
exactly like ``mangaeasy <command>``. Double-clicking it (no arguments)
opens the control center instead of dumping CLI help into a console.

Built with console=False so the exe runs in the Windows GUI subsystem:
no terminal window appears and the taskbar icon shows correctly.
CLI output goes to stderr/stdout which are captured when a caller pipes them;
when launched by the user directly (app mode), output goes to devnull since
all log output is streamed to the browser's Terminal tab via SSE.
"""

import multiprocessing
import os
import sys


def _redirect_stdio_to_devnull():
    """Suppress stdout/stderr when frozen in GUI subsystem (app mode only)."""
    try:
        devnull = open(os.devnull, "w")
        sys.stdout = devnull
        sys.stderr = devnull
    except Exception:
        pass


from mangaeasy.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    argv = sys.argv[1:]
    if not argv:
        argv = ["app"]
    if argv[0] == "app" and getattr(sys, "frozen", False):
        _redirect_stdio_to_devnull()
    sys.exit(main(argv))
