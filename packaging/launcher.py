"""PyInstaller entry point for the standalone mangaEasy backend executable.

The frozen exe *is* the CLI: ``mangaeasy.exe <command> [args...]`` behaves
exactly like ``mangaeasy <command>``. This build is meant to be invoked with
explicit args — either embedded inside the Electron desktop app's resources
(which always passes a command) or run directly from a terminal by a power
user. There is no GUI mode here anymore: the Electron app is the only
double-click-and-go launcher; this exe with no args just prints --help.

Built with console=False so a stray double-click doesn't pop a console
window — stdout/stderr are still captured normally when something pipes them.
"""

import multiprocessing
import sys

from mangaeasy.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main(sys.argv[1:]))
