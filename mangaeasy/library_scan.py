"""`mangaeasy library-list` — discover projects and per-item readiness.

State discovery for scripts and AI agents (and eventually the desktop app):
answers "what projects exist under this project root, and how far along is
each chapter/item" without opening the GUI. Read-only — never writes.

Handles both on-disk layouts:
- item pipeline:  library/<project>/<NN>/{panels/, narration.json[, intro.json]}
- legacy chapter: library/<name>/<NN>/{panels/, narration_<NN>.json, audio/, *.mp4}
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".aac"}


def _read_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def library_dir(project_root: Path) -> Path:
    """Mirror of the desktop app's `libraryDir` (config.ts): the configured
    `paths.library_subdir` from config.system.json, else the first existing
    legacy folder name, else `mangas`."""
    system_config = _read_json(project_root / "config.system.json")
    configured = (system_config.get("paths") or {}).get("library_subdir")
    if configured:
        return project_root / configured
    for candidate in ("mangas", "library", "manga"):
        full = project_root / candidate
        if full.is_dir():
            return full
    return project_root / "mangas"


def _count_files(folder: Path, extensions: set[str]) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for p in folder.iterdir() if p.is_file() and p.suffix.lower() in extensions)


def _narration_info(item_dir: Path) -> tuple[str | None, int]:
    """Find the item's narration file (either layout); return (name, entries)."""
    candidates = [item_dir / "narration.json", item_dir / f"narration_{item_dir.name}.json"]
    for path in candidates:
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8-sig") as f:
                data = json.load(f)
            return path.name, len(data) if isinstance(data, list) else 0
        except (OSError, ValueError):
            return path.name, 0
    return None, 0


def scan_item(item_dir: Path) -> dict:
    narration_file, narration_entries = _narration_info(item_dir)
    return {
        "item": item_dir.name,
        "path": str(item_dir),
        "panels": _count_files(item_dir / "panels", IMAGE_EXTENSIONS),
        "download": _count_files(item_dir / "download", IMAGE_EXTENSIONS),
        "narration_file": narration_file,
        "narration_entries": narration_entries,
        "has_intro": (item_dir / "intro.json").exists(),
        # Legacy chapter layout keeps audio + rendered mp4s inside the item
        # dir; the item pipeline writes them under --audio-root/--output-root
        # instead (use video-check/video-validate for those).
        "local_audio": _count_files(item_dir / "audio", AUDIO_EXTENSIONS),
        "local_videos": sorted(p.name for p in item_dir.glob("*.mp4")),
    }


def scan_project(project_dir: Path) -> dict:
    item_dirs = sorted(
        (p for p in project_dir.iterdir() if p.is_dir() and p.name[:1].isdigit()),
        key=lambda p: p.name,
    )
    # manga.json is written by `mangaeasy download` (source site, title URL,
    # manga_id, downloaded chapters) — see mangaeasy/download/mangadex.py.
    manga = _read_json(project_dir / "manga.json")
    return {
        "project": project_dir.name,
        "path": str(project_dir),
        "manga": manga or None,
        "items": [scan_item(item) for item in item_dirs],
    }


def scan_library(project_root: Path) -> dict:
    lib = library_dir(project_root)
    projects = []
    if lib.is_dir():
        for entry in sorted(lib.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_dir() and not entry.name.startswith("."):
                projects.append(scan_project(entry))
    return {"project_root": str(project_root), "library": str(lib), "projects": projects}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List projects and per-item readiness under a project root (read-only)."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Folder whose library/ (or configured library_subdir) gets scanned. Default: cwd.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit one JSON object on stdout instead of the human report.",
    )
    args = parser.parse_args()

    report = scan_library(args.project_root.resolve())
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False))
        return 0

    print(f"Library: {report['library']}")
    if not report["projects"]:
        print("  (no projects found)")
        return 0
    for project in report["projects"]:
        print(f"\n{project['project']}  ({len(project['items'])} item(s))")
        manga = project.get("manga") or {}
        if manga.get("title"):
            print(f"  title:  {manga['title']}")
        if manga.get("url"):
            print(f"  source: {manga['url']}")
        for item in project["items"]:
            narr = (
                f"{item['narration_entries']} entries"
                if item["narration_file"]
                else "no narration"
            )
            extras = []
            if item["has_intro"]:
                extras.append("intro")
            if item["local_audio"]:
                extras.append(f"{item['local_audio']} audio")
            if item["local_videos"]:
                extras.append(f"{len(item['local_videos'])} video(s)")
            suffix = f"  [{', '.join(extras)}]" if extras else ""
            print(f"  {item['item']}: {item['panels']} panel(s), {narr}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
