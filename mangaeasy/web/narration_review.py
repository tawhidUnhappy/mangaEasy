#!/usr/bin/env python3
"""mangaeasy.web.narration_review — Flask UI for reviewing AI-generated narration with audio preview.

Plays each panel's audio while displaying the matching panel image, then lets the user
leave per-panel feedback notes that are saved to ./tmp/{start}_{end}_Note_{name}.json.
"""

import json
import os
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

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_AUDIO_EXTS = {".wav", ".mp3", ".flac", ".m4a", ".ogg"}

TMP_DIR = PROJECT_ROOT / "tmp"


def _sorted_chapter_dirs(manga_root: Path) -> list[Path]:
    return sorted(
        [d for d in manga_root.iterdir() if d.is_dir() and d.name[0].isdigit()],
        key=lambda p: int(p.name) if p.name.isdigit() else p.name,
    )


def _has_images(d: Path) -> bool:
    try:
        return any(f.suffix.lower() in _IMAGE_EXTS for f in d.iterdir() if f.is_file())
    except Exception:
        return False


def _has_audio(d: Path) -> bool:
    try:
        return any(f.suffix.lower() in _AUDIO_EXTS for f in d.iterdir() if f.is_file())
    except Exception:
        return False


def _load_segments(
    manga_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Path], dict[str, Path], str, str]:
    syscfg = load_system_config()
    path_cfg = syscfg.get("paths", {})
    panels_subdir    = path_cfg.get("panels_subdir",    "panels")
    processed_subdir = path_cfg.get("processed_subdir", "panels_processed")
    audio_subdir     = path_cfg.get("audio_subdir",     "audio")
    upscale_on       = bool(syscfg.get("process_panels", {}).get("upscale", False))

    segments:   list[dict[str, Any]] = []
    image_dirs: dict[str, Path]      = {}
    audio_dirs: dict[str, Path]      = {}

    ch_dirs = _sorted_chapter_dirs(manga_root)
    if not ch_dirs:
        return [], {}, {}, "00", "00"

    start_ch = ch_dirs[0].name
    end_ch   = ch_dirs[-1].name

    for ch_dir in ch_dirs:
        ch = ch_dir.name

        # Resolve image folder — prefer processed if upscale is on and folder has images
        proc_dir = ch_dir / processed_subdir
        raw_dir  = ch_dir / panels_subdir
        image_root = (
            proc_dir if upscale_on and proc_dir.exists() and _has_images(proc_dir)
            else raw_dir
        )

        # Resolve audio folder — prefer faded (fade-in/out applied)
        faded_dir = ch_dir / "audio_faded"
        raw_audio = ch_dir / audio_subdir
        audio_root = (
            faded_dir if faded_dir.exists() and _has_audio(faded_dir)
            else raw_audio
        )

        if not image_root.exists() or not audio_root.exists():
            print(f"[WARN] Chapter {ch}: missing panels or audio folder, skipping.")
            continue

        # Load narration JSON for context display
        narration_file = ch_dir / f"narration_{ch}.json"
        narration_map: dict[str, str] = {}
        if narration_file.exists():
            try:
                with narration_file.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        img = item.get("image", "")
                        if img:
                            narration_map[img] = item.get("narration", "")
            except Exception as exc:
                print(f"[WARN] Failed to load {narration_file}: {exc}")

        # Build stem → filename maps for images and audio
        images: dict[str, str] = {}
        for f in image_root.iterdir():
            if f.is_file() and f.suffix.lower() in _IMAGE_EXTS:
                images[f.stem] = f.name

        audios: dict[str, str] = {}
        for f in audio_root.iterdir():
            if f.is_file() and f.suffix.lower() in _AUDIO_EXTS:
                audios[f.stem] = f.name

        # Only segments that have BOTH image and audio
        matched_stems = sorted(set(images) & set(audios), key=numeric_sort_key)
        if not matched_stems:
            print(f"[WARN] Chapter {ch}: no matched image+audio pairs, skipping.")
            continue

        image_dirs[ch] = image_root
        audio_dirs[ch] = audio_root

        for stem in matched_stems:
            img_name = images[stem]
            aud_name = audios[stem]
            segments.append({
                "image":     img_name,
                "chapter":   ch,
                "narration": narration_map.get(img_name, ""),
                "audio_url": f"/audio/{ch}/{aud_name}",
                "image_url": f"/images/{ch}/{img_name}",
            })

        print(f"[INFO] Chapter {ch}: {len(matched_stems)} pairs  (audio: {audio_root.name})")

    return segments, image_dirs, audio_dirs, start_ch, end_ch


# ── Global state ───────────────────────────────────────────────────────────────

MANGA_ROOT = manga_dir(_name)
SEGMENTS, IMAGE_DIRS, AUDIO_DIRS, START_CH, END_CH = _load_segments(MANGA_ROOT)

NOTES_FILE = TMP_DIR / f"{START_CH}_{END_CH}_Note_{_name}.json"
NOTES: dict[str, str] = {}   # image filename → note text


def _load_notes() -> None:
    global NOTES
    if NOTES_FILE.exists():
        try:
            with NOTES_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                NOTES = {item["image"]: item.get("note", "") for item in data if item.get("image")}
            print(f"[INFO] Loaded {len(NOTES)} existing note(s) from {NOTES_FILE.name}")
        except Exception as exc:
            print(f"[WARN] Failed to load notes file, starting fresh: {exc}")
            NOTES = {}


def _save_notes() -> None:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    # Emit only non-empty notes, ordered by segment appearance
    out: list[dict] = []
    for seg in SEGMENTS:
        img  = seg["image"]
        note = NOTES.get(img, "").strip()
        if note:
            out.append({
                "image":     img,
                "chapter":   seg["chapter"],
                "narration": seg["narration"],
                "note":      note,
            })
    with NOTES_FILE.open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)


_load_notes()

print(f"[INFO] Total segments: {len(SEGMENTS)}  |  Notes file: {NOTES_FILE}")

# ── Flask app ──────────────────────────────────────────────────────────────────

app = make_app(__name__)
app.secret_key = "narration-review-secret"
register_shutdown(app)


@app.route("/")
def root():
    return render_template(
        "narration_review.html",
        manga_name=_name,
        total=len(SEGMENTS),
        start_ch=START_CH,
        end_ch=END_CH,
    )


@app.route("/api/segments")
def api_segments():
    return jsonify({"status": "ok", "segments": SEGMENTS, "total": len(SEGMENTS)})


@app.route("/api/notes")
def api_notes():
    return jsonify({"status": "ok", "notes": NOTES})


@app.route("/api/notes/save", methods=["POST"])
def api_notes_save():
    try:
        data  = request.get_json(silent=True) or {}
        image = str(data.get("image", "")).strip()
        note  = str(data.get("note",  "")).strip()
        if not image:
            return jsonify({"status": "error", "msg": "Missing image"}), 400
        if note:
            NOTES[image] = note
        elif image in NOTES:
            del NOTES[image]
        _save_notes()
        return jsonify({"status": "ok", "saved_to": str(NOTES_FILE), "note_count": len(NOTES)})
    except Exception as exc:
        return jsonify({"status": "error", "msg": str(exc)}), 500


@app.route("/audio/<chapter>/<path:filename>")
def serve_audio(chapter: str, filename: str):
    adir = AUDIO_DIRS.get(chapter)
    if adir is None or not adir.exists():
        return "Chapter not found", 404
    return send_from_directory(adir, filename)


@app.route("/images/<chapter>/<path:filename>")
def serve_image(chapter: str, filename: str):
    idir = IMAGE_DIRS.get(chapter)
    if idir is None or not idir.exists():
        return "Chapter not found", 404
    return send_from_directory(idir, filename)


def main() -> None:
    if not MANGA_ROOT.exists():
        print(f"[ERROR] Manga folder not found: {MANGA_ROOT}")
        return
    if not SEGMENTS:
        print("[ERROR] No segments found — make sure audio files exist alongside panel images.")
        return

    port = int(load_system_config().get("ports", {}).get("narration_review", 5006))
    print(f"[INFO] Narration review at http://127.0.0.1:{port}/")
    print("[INFO] Shortcuts: Space=play/pause | ← → = prev/next segment | Ctrl+S = save note")
    run_app(app, port)


if __name__ == "__main__":
    main()
