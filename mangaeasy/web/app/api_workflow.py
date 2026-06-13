"""mangaeasy.web.app.api_workflow — the guided chapter workflow.

One endpoint backs the "Make a video" tab: it reads/writes the download
settings in config.json and reports per-step progress (pages downloaded,
panels cut, narration written, audio generated) so the UI can show how far
along the current chapter is.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

from mangaeasy.web.app import jobs
from mangaeasy.web.app.api_project import _read_json
from mangaeasy.web.app.state import lock, log, state

bp = Blueprint("workflow", __name__)

IMAGE_EXTS  = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_CACHE_FILE = ".mdx_cache.json"   # written by mangadex.py inside <ch_dir>/


def _library_dir(root: Path, sys_cfg: dict) -> Path:
    """Mirror of mangaeasy.paths.library_dir, but for the *selected* project.

    The app process resolved its own PROJECT_ROOT at startup, so the package
    helper would point at the wrong folder — recompute against `root`.
    """
    sub = (sys_cfg.get("paths") or {}).get("library_subdir")
    if sub:
        return root / sub
    library = root / "library"
    legacy = root / "manga"
    if legacy.is_dir() and not library.is_dir():
        return legacy
    return library


def _count_files(folder: Path, exts: set[str]) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for p in folder.iterdir() if p.suffix.lower() in exts)


def _read_chapter_cache(ch_dir: Path) -> dict | None:
    p = ch_dir / _CACHE_FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


@bp.route("/api/workflow", methods=["GET", "POST"])
def api_workflow():
    root: Path = state["project_root"]
    cfg_path = root / "config.json"

    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        cfg = _read_json(cfg_path) or {}
        dl = cfg.get("download") if isinstance(cfg.get("download"), dict) else {}
        if "manga_id" in body:
            dl["manga_id"] = str(body["manga_id"]).strip()
        if "name" in body:
            dl["name"] = str(body["name"]).strip()
        if "chapter" in body:
            try:
                dl["chapter"] = int(body["chapter"])
            except (TypeError, ValueError):
                pass
        if "language" in body:
            dl["translated_language"] = str(body["language"]).strip() or "en"
        cfg["download"] = dl
        cfg.pop("_comment", None)
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        log(f"[app] wrote {cfg_path}")

    cfg = _read_json(cfg_path) or {}
    sys_cfg = _read_json(root / "config.system.json") or {}
    dl = cfg.get("download") if isinstance(cfg.get("download"), dict) else {}

    name = str(dl.get("name") or "")
    try:
        chapter = int(dl.get("chapter") or 1)
    except (TypeError, ValueError):
        chapter = 1
    language = str(
        dl.get("translated_language")
        or (sys_cfg.get("download_defaults") or {}).get("translated_language")
        or "en"
    )

    info: dict = {
        "manga_id": str(dl.get("manga_id") or ""),
        "name": name,
        "chapter": chapter,
        "language": language,
        "bgm_set": bool((sys_cfg.get("bgm") or {}).get("file")),
        "voice_set": bool((sys_cfg.get("tts") or {}).get("speaker_wav")),
        "paths": None,
        "status": None,
    }
    if not name:
        return jsonify(info)

    paths_cfg = sys_cfg.get("paths") or {}
    ch_dir = _library_dir(root, sys_cfg) / name / f"{chapter:02d}"
    download_dir = ch_dir / "download"
    panels_dir = ch_dir / paths_cfg.get("panels_subdir", "panels")
    audio_dir = ch_dir / paths_cfg.get("audio_subdir", "audio")
    narration = ch_dir / f"narration_{chapter:02d}.json"
    video = ch_dir / f"{chapter:02d}_{name}.mp4"
    video_bgm = ch_dir / f"{chapter:02d}_{name}_with_bgm.mp4"

    narration_items = 0
    if narration.exists():
        try:
            data = json.loads(narration.read_text(encoding="utf-8-sig"))
            narration_items = len(data) if isinstance(data, list) else 0
        except Exception:
            narration_items = 0

    info["paths"] = {
        "chapter": str(ch_dir),
        "download": str(download_dir),
        "panels": str(panels_dir),
        "audio": str(audio_dir),
        "narration": str(narration),
    }
    info["status"] = {
        "downloads": _count_files(download_dir, IMAGE_EXTS),
        "panels": _count_files(panels_dir, IMAGE_EXTS),
        "narration": narration.exists(),
        "narration_items": narration_items,
        "audio": _count_files(audio_dir, {".wav"}),
        "video": video_bgm.exists() or video.exists(),
    }
    return jsonify(info)


@bp.route("/api/workflow/chapters", methods=["GET"])
def api_workflow_chapters():
    """Scan the manga library folder and return per-chapter progress counts."""
    root: Path = state["project_root"]
    cfg = _read_json(root / "config.json") or {}
    sys_cfg = _read_json(root / "config.system.json") or {}
    dl = cfg.get("download") if isinstance(cfg.get("download"), dict) else {}

    name = str(dl.get("name") or "")
    if not name:
        return jsonify({"chapters": [], "name": ""})

    lib_dir = _library_dir(root, sys_cfg)
    manga_dir = lib_dir / name
    if not manga_dir.is_dir():
        return jsonify({"chapters": [], "name": name})

    paths_cfg = sys_cfg.get("paths") or {}
    panels_sub = paths_cfg.get("panels_subdir", "panels")
    audio_sub  = paths_cfg.get("audio_subdir",  "audio")

    chapters = []
    for ch_dir in sorted(manga_dir.iterdir()):
        if not ch_dir.is_dir():
            continue
        try:
            ch_num = int(ch_dir.name)
        except ValueError:
            continue
        cache    = _read_chapter_cache(ch_dir)
        expected = cache.get("total") if cache else None
        chapters.append({
            "chapter":    ch_num,
            "downloaded": _count_files(ch_dir / "download", IMAGE_EXTS),
            "expected":   expected,    # total pages known from MangaDex (None = never fetched)
            "cached":     cache is not None,
            "panels":     _count_files(ch_dir / panels_sub,  IMAGE_EXTS),
            "audio":      _count_files(ch_dir / audio_sub,   {".wav"}),
            "video":      bool(list(ch_dir.glob("*.mp4"))),
        })

    return jsonify({"chapters": chapters, "name": name})


@bp.route("/api/workflow/chapters/<int:chapter_num>/cache", methods=["DELETE"])
def api_clear_chapter_cache(chapter_num: int):
    """Delete the cached MangaDex metadata for one chapter.

    The next download will re-fetch chapter ID and image list from the API.
    """
    root: Path = state["project_root"]
    cfg = _read_json(root / "config.json") or {}
    sys_cfg = _read_json(root / "config.system.json") or {}
    dl = cfg.get("download") if isinstance(cfg.get("download"), dict) else {}
    name = str(dl.get("name") or "")
    if not name:
        return jsonify({"error": "no manga name configured"}), 400

    lib = _library_dir(root, sys_cfg)
    ch_dir = lib / name / f"{chapter_num:02d}"
    cache_file = ch_dir / _CACHE_FILE

    if cache_file.exists():
        cache_file.unlink()
        log(f"[cache] cleared ch{chapter_num:02d} cache")
        return jsonify({"cleared": True})
    return jsonify({"cleared": False, "note": "no cache file found"})


@bp.route("/api/workflow/chapters/<int:chapter_num>/delete", methods=["POST"])
def api_delete_chapter_data(chapter_num: int):
    """Delete one or more stages of a chapter's generated files.

    Body: {"what": "download" | "panels" | "audio" | "video" | "all"}
    narration_*.json is never touched — it contains user-authored content.
    """
    body = request.get_json(silent=True) or {}
    what = str(body.get("what", "")).strip()
    valid = {"download", "panels", "audio", "video", "all"}
    if what not in valid:
        return jsonify({"error": f"'what' must be one of: {', '.join(sorted(valid))}"}), 400

    root: Path = state["project_root"]
    cfg = _read_json(root / "config.json") or {}
    sys_cfg = _read_json(root / "config.system.json") or {}
    dl = cfg.get("download") if isinstance(cfg.get("download"), dict) else {}
    name = str(dl.get("name") or "")
    if not name:
        return jsonify({"error": "no manga name configured"}), 400

    lib = _library_dir(root, sys_cfg)
    ch_dir = lib / name / f"{chapter_num:02d}"
    if not ch_dir.is_dir():
        return jsonify({"error": f"chapter {chapter_num:02d} folder not found"}), 404

    paths_cfg = sys_cfg.get("paths") or {}
    panels_sub = paths_cfg.get("panels_subdir", "panels")
    audio_sub = paths_cfg.get("audio_subdir", "audio")

    removed: list[str] = []

    def rm_tree(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(path.name + "/")

    def rm_glob(pattern: str) -> None:
        files = list(ch_dir.glob(pattern))
        for f in files:
            f.unlink(missing_ok=True)
        if files:
            removed.append(f"{len(files)}×{pattern}")

    if what in ("download", "all"):
        rm_tree(ch_dir / "download")
    if what in ("panels", "all"):
        rm_tree(ch_dir / panels_sub)
    if what in ("audio", "all"):
        rm_tree(ch_dir / audio_sub)
    if what in ("video", "all"):
        rm_glob("*.mp4")
    if what == "all":
        rm_tree(ch_dir / "work")

    log(f"[delete] ch{chapter_num:02d} {what}: {', '.join(removed) or 'nothing to remove'}")
    return jsonify({"deleted": removed, "chapter": chapter_num})


@bp.route("/api/workflow/batch-download", methods=["POST"])
def api_batch_download():
    """Download a range of chapters from MangaDex one by one as a single job."""
    body = request.get_json(silent=True) or {}
    try:
        start = int(body.get("start", 1))
        end   = int(body.get("end",   1))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid chapter range"}), 400

    if start < 1 or end < start or end > 999:
        return jsonify({"error": "range must be 1–999 and start ≤ end"}), 400

    fresh: bool = bool(body.get("fresh", False))

    root: Path = state["project_root"]
    cfg_path = root / "config.json"
    total = end - start + 1

    job: dict = {
        "kind":   "batch-download",
        "name":   f"ch {start:02d}–{end:02d}",
        "thread": None,
        "proc":   None,
    }

    def work() -> None:
        # Resolve manga dir once so we can skip already-downloaded chapters.
        cfg0    = _read_json(cfg_path) or {}
        sys_cfg = _read_json(root / "config.system.json") or {}
        dl0     = cfg0.get("download") if isinstance(cfg0.get("download"), dict) else {}
        name    = str(dl0.get("name") or "")
        lib     = _library_dir(root, sys_cfg) if name else None

        downloaded_count = 0
        for i, ch in enumerate(range(start, end + 1), 1):
            # Skip chapters whose download folder is already populated.
            if lib and name:
                dl_dir = lib / name / f"{ch:02d}" / "download"
                if _count_files(dl_dir, IMAGE_EXTS) > 0:
                    log(f"[batch-download] chapter {ch:02d} already downloaded — skipping")
                    continue

            log(f"[batch-download] chapter {ch:02d} ({i}/{total})…")

            # Update config.json so the download command picks up the right chapter.
            cfg = _read_json(cfg_path) or {}
            dl  = cfg.get("download") if isinstance(cfg.get("download"), dict) else {}
            dl["chapter"] = ch
            cfg["download"] = dl
            cfg.pop("_comment", None)
            cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

            dl_args = ["--fresh"] if fresh else []
            proc = jobs.spawn_cli("download", dl_args, root)
            job["proc"] = proc
            assert proc.stdout is not None
            for line in proc.stdout:
                log(line.rstrip("\n"))
            code = proc.wait()
            log(f"[download] chapter {ch:02d} exit {code}")
            if code != 0:
                log(f"[batch-download] stopped — chapter {ch:02d} failed")
                return

            downloaded_count += 1

            # Polite pause between chapters so we don't hammer MangaDex.
            if ch < end:
                pause = 10
                log(f"[batch-download] pausing {pause}s before next chapter…")
                time.sleep(pause)

        log(f"[batch-download] chapters {start:02d}–{end:02d} done ✓"
            f" ({downloaded_count} downloaded)")

    thread = threading.Thread(target=work, daemon=True)
    job["thread"] = thread
    with lock:
        if jobs.job_running():
            return jsonify({"error": "another job is already running"}), 409
        state["job"] = job
        thread.start()

    return jsonify({"started": True})
