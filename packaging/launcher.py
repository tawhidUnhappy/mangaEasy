"""PyInstaller entry point for the standalone MediaConductor executable.

The frozen executable *is* the CLI: ``mediaconductor <command> [args...]``.
It also serves MCP over stdio via ``mediaconductor mcp``. With no arguments it
prints the curated help instead of starting a second process or a GUI.
"""

import multiprocessing
import sys

from mangaeasy.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main(sys.argv[1:]))
