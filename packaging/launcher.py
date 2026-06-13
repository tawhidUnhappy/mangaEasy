"""PyInstaller entry point for the standalone mangaEasy executable.

The frozen exe *is* the CLI: ``mangaEasy.exe <command> [args...]`` behaves
exactly like ``mangaeasy <command>``. Double-clicking it (no arguments)
opens the control center instead of dumping CLI help into a console.
"""

import multiprocessing
import sys


def _hide_console():
    """Hide the Windows console window when launching the GUI app."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


from mangaeasy.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    argv = sys.argv[1:]
    if not argv:
        argv = ["app"]
    if argv[0] == "app":
        _hide_console()
    sys.exit(main(argv))
