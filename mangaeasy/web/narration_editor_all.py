#!/usr/bin/env python3
"""mangaeasy.web.narration_editor_all — Flask UI for editing narration across ALL chapters."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from flask import jsonify, render_template, request, send_from_directory

from mangaeasy.config import PROJECT_ROOT, load_download_config, load_system_config
from mangaeasy.paths import manga_dir
from mangaeasy.utils import numeric_sort_key
from mangaeasy.web.flask_utils import make_app, register_shutdown, run_app

os.environ.setdefault("OMP_NUM_THREADS", "1")

_dl   = load_download_config()
_name = str(_dl["name"])

_w_cfg = load_system_config().get("whisper", {})
DEFAULT_MODEL   = os.environ.get("WHISPER_MODEL",   _w_cfg.get("model",   "medium"))
DEFAULT_DEVICE  = os.environ.get("WHISPER_DEVICE",  _w_cfg.get("device",  "auto")).lower().strip()
DEFAULT_COMPUTE = os.environ.get("WHISPER_COMPUTE", _w_cfg.get("compute", "float16"))

from faster_whisper import WhisperModel


def _sorted_chapter_dirs(manga_root: Path) -> list[Path]:
    return sorted(
        [d for d in manga_root.iterdir() if d.is_dir() and d.name[0].isdigit()],
        key=lambda p: int(p.name) if p.name.isdigit() else p.name,
    )


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _load_all_chapters(manga_root: Path) -> tuple[
    list[dict[str, Any]],
    dict[str, Path],
    dict[str, Path],
]:
    path_cfg = load_system_config().get("paths", {})
    panels_subdir = path_cfg.get("panels_subdir", "panels")

    all_items: list[dict[str, Any]] = []
    chapter_json_paths: dict[str, Path] = {}
    chapter_panel_dirs: dict[str, Path] = {}

    for ch_dir in _sorted_chapter_dirs(manga_root):
        narration_file  = ch_dir / f"narration_{ch_dir.name}.json"
        panels_folder   = ch_dir / panels_subdir

        # Load existing narration entries keyed by image filename
        existing: dict[str, dict[str, Any]] = {}
        if narration_file.exists():
            try:
                with narration_file.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data = [data]
                if isinstance(data, list):
                    for item in data:
                        img = item.get("image", "")
                        if img:
                            existing[img] = item
            except Exception as exc:
                print(f"[WARN] Failed to load {narration_file}: {exc}")

        # Scan the panels folder for every image file
        disk_images: list[str] = []
        if panels_folder.exists():
            disk_images = [
                f.name for f in panels_folder.iterdir()
                if f.is_file() and f.suffix.lower() in _IMAGE_EXTS
            ]

        # Nothing at all for this chapter — skip
        if not disk_images and not existing:
            continue

        # Drop narration entries whose panel image no longer exists on disk
        seen      = set(disk_images)
        json_only = [k for k in existing if k not in seen]
        if json_only:
            print(f"[INFO] Ch.{ch_dir.name}: removing {len(json_only)} orphan entry(s) not found in panels folder: {json_only}")
            for k in json_only:
                del existing[k]
            if narration_file.exists():
                cleaned = sorted(existing.values(), key=lambda it: numeric_sort_key(it.get("image", "")))
                try:
                    with narration_file.open("w", encoding="utf-8") as f:
                        json.dump(cleaned, f, indent=2, ensure_ascii=False)
                except Exception as exc:
                    print(f"[WARN] Ch.{ch_dir.name}: failed to rewrite narration.json: {exc}")

        all_images = sorted(disk_images, key=numeric_sort_key)

        # Auto-create / repopulate file on disk if it has no valid entries yet
        if not existing and all_images:
            initial = [{"image": img, "narration": ""} for img in all_images]
            try:
                narration_file.parent.mkdir(parents=True, exist_ok=True)
                with narration_file.open("w", encoding="utf-8") as f:
                    json.dump(initial, f, indent=2, ensure_ascii=False)
                existing = {item["image"]: item for item in initial}
                print(f"[INFO] Ch.{ch_dir.name}: initialized {narration_file.name} with {len(initial)} entries")
            except Exception as exc:
                print(f"[WARN] Ch.{ch_dir.name}: could not auto-create {narration_file.name}: {exc}")

        ch = ch_dir.name
        chapter_json_paths[ch] = narration_file
        chapter_panel_dirs[ch] = panels_folder

        new_count = 0
        for img_name in all_images:
            if img_name in existing:
                all_items.append({**existing[img_name], "_chapter": ch})
            else:
                all_items.append({"image": img_name, "narration": "", "_chapter": ch})
                new_count += 1

        if new_count:
            print(f"[INFO] Ch.{ch}: {new_count} panel(s) on disk not in narration.json — added as empty")

    return all_items, chapter_json_paths, chapter_panel_dirs


def _compute_chapter_ranges(narrations: list[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    ranges: dict[str, tuple[int, int]] = {}
    prev_ch: str | None = None
    start = 0
    for i, item in enumerate(narrations):
        ch = item["_chapter"]
        if ch != prev_ch:
            if prev_ch is not None:
                ranges[prev_ch] = (start, i)
            start = i
            prev_ch = ch
    if prev_ch is not None:
        ranges[prev_ch] = (start, len(narrations))
    return ranges


MANGA_ROOT = manga_dir(_name)
NARRATIONS, CHAPTER_JSON_PATHS, CHAPTER_PANEL_DIRS = _load_all_chapters(MANGA_ROOT)
CHAPTER_RANGES = _compute_chapter_ranges(NARRATIONS)

app = make_app(__name__)
app.secret_key = "narration-editor-all-secret"
register_shutdown(app)

whisper_model: WhisperModel | None = None
WHISPER_ACTIVE_MODEL   = DEFAULT_MODEL
WHISPER_ACTIVE_DEVICE  = None
WHISPER_ACTIVE_COMPUTE = None


def init_whisper_auto() -> None:
    global whisper_model, WHISPER_ACTIVE_DEVICE, WHISPER_ACTIVE_COMPUTE, WHISPER_ACTIVE_MODEL

    def _try(device: str, compute: str) -> bool:
        global whisper_model
        try:
            whisper_model = WhisperModel(DEFAULT_MODEL, device=device, compute_type=compute)
            return True
        except Exception as exc:
            print(f"[WARN] Whisper init failed: {device}/{compute} | {exc}")
            return False

    if DEFAULT_DEVICE == "cpu":
        if _try("cpu", "int8"):
            WHISPER_ACTIVE_DEVICE  = "cpu"
            WHISPER_ACTIVE_COMPUTE = "int8"
        return

    gpu_candidates = list(dict.fromkeys([DEFAULT_COMPUTE, "float16", "int8_float16"]))
    if DEFAULT_DEVICE in ("auto", "cuda"):
        for comp in gpu_candidates:
            if _try("cuda", comp):
                WHISPER_ACTIVE_DEVICE  = "cuda"
                WHISPER_ACTIVE_COMPUTE = comp
                return
        if _try("cpu", "int8"):
            WHISPER_ACTIVE_DEVICE  = "cpu"
            WHISPER_ACTIVE_COMPUTE = "int8"


init_whisper_auto()
print(f"[INFO] Whisper: model={WHISPER_ACTIVE_MODEL} device={WHISPER_ACTIVE_DEVICE} compute={WHISPER_ACTIVE_COMPUTE}")
print(f"[INFO] Loaded {len(NARRATIONS)} panels from {len(CHAPTER_JSON_PATHS)} chapters")


def _save_chapter(chapter_name: str) -> None:
    json_path = CHAPTER_JSON_PATHS.get(chapter_name)
    if json_path is None:
        return
    start, end = CHAPTER_RANGES.get(chapter_name, (0, 0))
    items = [
        {k: v for k, v in item.items() if k != "_chapter"}
        for item in NARRATIONS[start:end]
    ]
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)


def _whisper_info() -> dict:
    return {"model": WHISPER_ACTIVE_MODEL, "device": WHISPER_ACTIVE_DEVICE, "compute": WHISPER_ACTIVE_COMPUTE}


@app.route("/")
def root():
    return render_template("narration_editor_all.html")


@app.route("/api/state")
def api_state():
    total = len(NARRATIONS)
    if total == 0:
        return jsonify({"status": "ok", "finished": True, "total": 0, "index": 0,
                        "item": None, "whisper": _whisper_info()})
    try:
        idx = max(0, min(int(request.args.get("i", "0")), total - 1))
    except ValueError:
        idx = 0
    item = NARRATIONS[idx]
    return jsonify({
        "status": "ok", "finished": False, "total": total, "index": idx,
        "item": {
            "image":     item.get("image", ""),
            "narration": item.get("narration", ""),
            "chapter":   item["_chapter"],
        },
        "whisper": _whisper_info(),
    })


@app.route("/api/panels")
def api_panels():
    """Lightweight panel list for the navigator sidebar."""
    panels = [
        {
            "index":        i,
            "chapter":      item["_chapter"],
            "image":        item.get("image", ""),
            "hasNarration": bool((item.get("narration") or "").strip()),
        }
        for i, item in enumerate(NARRATIONS)
    ]
    return jsonify({"status": "ok", "panels": panels})


@app.route("/api/update_text", methods=["POST"])
def api_update_text():
    try:
        data = request.get_json(silent=True) or {}
        idx  = int(data.get("index"))
        text = str(data.get("text", ""))
        if 0 <= idx < len(NARRATIONS):
            chapter_name = NARRATIONS[idx]["_chapter"]
            NARRATIONS[idx]["narration"] = text
            _save_chapter(chapter_name)
            return jsonify({"status": "ok"})
        return jsonify({"status": "error", "msg": "Index out of bounds"}), 400
    except Exception as exc:
        return jsonify({"status": "error", "msg": str(exc)}), 500


@app.route("/api/transcribe", methods=["POST"])
def api_transcribe():
    if whisper_model is None:
        return jsonify({"status": "error", "msg": "Whisper not initialized"}), 500
    if "audio" not in request.files:
        return jsonify({"status": "error", "msg": "Missing audio file"}), 400
    mode       = (request.form.get("mode") or "final").lower().strip()
    audio_file = request.files["audio"]
    audio_file.stream.seek(0, os.SEEK_END)
    if audio_file.stream.tell() > 25 * 1024 * 1024:
        return jsonify({"status": "error", "msg": "Audio too large (>25MB)"}), 413
    audio_file.stream.seek(0)
    tmp_path = None
    try:
        suffix = Path(audio_file.filename).suffix.lower() or ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name
            audio_file.save(tmp_path)
        beam_size = 1 if mode == "chunk" else 5
        segments, _ = whisper_model.transcribe(tmp_path, vad_filter=True, beam_size=beam_size)
        text = " ".join((s.text or "").strip() for s in segments).strip()
        return jsonify({"status": "ok", "text": text, "whisper": _whisper_info()})
    except Exception as exc:
        return jsonify({"status": "error", "msg": str(exc)}), 500
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.route("/images/<chapter>/<path:filename>")
def serve_image(chapter: str, filename: str):
    panel_dir = CHAPTER_PANEL_DIRS.get(chapter)
    if panel_dir is None or not panel_dir.exists():
        return "Chapter not found", 404
    return send_from_directory(panel_dir, filename)


def main() -> None:
    port = int(load_system_config().get("ports", {}).get("narration_editor_all", 5005))
    print(f"[INFO] Narration editor (all chapters) at http://127.0.0.1:{port}/")
    print("[INFO] Shortcut: INSERT => start/stop mic")
    run_app(app, port)


if __name__ == "__main__":
    main()
