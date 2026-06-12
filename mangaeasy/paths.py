"""mangaeasy.paths
Centralised path helpers so every module uses the same folder conventions.

Subdirectory names (panels, audio, processed) are read from
config.system.json → paths section so they can be changed without
touching any Python files.
"""

from pathlib import Path
from mangaeasy.config import PROJECT_ROOT, load_download_config, load_system_config


def _dl() -> dict:
    return load_download_config()


def _path_cfg() -> dict:
    return load_system_config().get("paths", {})


# ── Library / chapter dirs ────────────────────────────────────────────────────

def library_dir() -> Path:
    """The folder that holds every manga — one subfolder per title.

    Named library/ so it can't be confused with a single manga's own folder
    (library/{name}/ holds that manga's chapters). Renameable via
    config.system.json → paths.library_subdir. Projects created before the
    rename keep working: if ./manga exists and ./library does not, the legacy
    folder is used.
    """
    configured = _path_cfg().get("library_subdir")
    if configured:
        return PROJECT_ROOT / configured
    library = PROJECT_ROOT / "library"
    legacy = PROJECT_ROOT / "manga"
    if legacy.is_dir() and not library.is_dir():
        return legacy
    return library


def manga_dir(name: str | None = None) -> Path:
    """One manga's own folder (holds its chapter subfolders)."""
    if name is None:
        name = str(_dl()["name"])
    return library_dir() / name


def chapter_dir(name: str | None = None, chapter: int | None = None) -> Path:
    if name is None or chapter is None:
        dl = _dl()
        name    = name    or str(dl["name"])
        chapter = chapter if chapter is not None else int(dl["chapter"])
    return manga_dir(name) / f"{chapter:02d}"


def download_dir(name=None, chapter=None) -> Path:
    return chapter_dir(name, chapter) / "download"


def panels_dir(name=None, chapter=None) -> Path:
    subdir = _path_cfg().get("panels_subdir", "panels")
    return chapter_dir(name, chapter) / subdir


def processed_panels_dir(name=None, chapter=None) -> Path:
    """Upscaled / mirrored / cleaned panels produced by mangaeasy process-panels."""
    subdir = _path_cfg().get("processed_subdir", "panels_processed")
    return chapter_dir(name, chapter) / subdir


def audio_dir(name=None, chapter=None) -> Path:
    subdir = _path_cfg().get("audio_subdir", "audio")
    return chapter_dir(name, chapter) / subdir


def narration_json(name=None, chapter=None) -> Path:
    if name is None or chapter is None:
        dl = _dl()
        name    = name    or str(dl["name"])
        chapter = chapter if chapter is not None else int(dl["chapter"])
    return chapter_dir(name, chapter) / f"narration_{chapter:02d}.json"


def output_video(name=None, chapter=None) -> Path:
    dl = _dl()
    n = name    or str(dl["name"])
    c = chapter if chapter is not None else int(dl["chapter"])
    return chapter_dir(n, c) / f"{c:02d}_{n}.mp4"


# ── Temp dirs ─────────────────────────────────────────────────────────────────

def tmp_dir(name=None, chapter=None) -> Path:
    """Temporary build artefacts live inside the chapter folder (library/{name}/{ch}/tmp/).

    Everything for a chapter stays together; the folder is removed after
    render unless config.system.json → render.keep_tmp is true.
    """
    return chapter_dir(name, chapter) / "tmp"


def faded_audio_dir(name=None, chapter=None) -> Path:
    return chapter_dir(name, chapter) / "audio_faded"
