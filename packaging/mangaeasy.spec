# PyInstaller spec for the standalone mangaEasy build (one-folder).
#
#   uv run pyinstaller packaging/mangaeasy.spec
#
# Produces dist/mangaEasy/ with the mangaeasy executable inside. The whole
# mangaeasy package is also shipped as real source files (datas) because:
#   * subcommand modules are imported lazily by name, and
#   * the external AI tool envs (IndexTTS / Kokoro) execute package scripts
#     like mangaeasy/audio/tts.py with their own Python interpreters,
# so the files must exist on disk, not only inside the frozen archive.

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # repo root (spec lives in packaging/)
PKG  = ROOT / "mangaeasy"
ICON = ROOT / "packaging" / "icon.ico"

hiddenimports = collect_submodules("mangaeasy") + collect_submodules("webview")

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(PKG), "mangaeasy"),
        (str(ROOT / "packaging" / "icon.png"), "."),  # bundled for Linux/macOS window icon
    ],
    hiddenimports=hiddenimports,
    excludes=["torch", "torchvision", "transformers", "faster_whisper"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mangaeasy",
    console=True,   # keep True so CLI commands show output in their terminal;
                    # launcher.py hides the window when starting the GUI app
    icon=str(ICON),
    upx=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="mangaEasy",
)
