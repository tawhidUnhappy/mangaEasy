#!/usr/bin/env python3
"""mangaeasy.web.panel_editor — Flask UI for webtoon panel editor with auto-gutter detection."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Response, jsonify, render_template, request, send_from_directory
from PIL import Image

from mangaeasy.config import PROJECT_ROOT, load_download_config
from mangaeasy.panels.gutter import load_gutter_config, auto_detect_panels
from mangaeasy.paths import chapter_dir
from mangaeasy.utils import numeric_sort_key
from mangaeasy.web.flask_utils import make_app, register_shutdown, run_app

Image.MAX_IMAGE_PIXELS = None

_CONFIG_FILE = PROJECT_ROOT / "config.json"
_dl          = load_download_config()
_chapter     = int(_dl["chapter"])
_name        = str(_dl["name"])

IMAGE_DIR = chapter_dir(_name, _chapter) / "download"
PANEL_DIR = chapter_dir(_name, _chapter) / "panels"
PANEL_DIR.mkdir(parents=True, exist_ok=True)

app = make_app(__name__, static_url_path="/static")
register_shutdown(app)

IMAGE_META:   List[Dict[str, Any]] = []
TOTAL_HEIGHT: int = 0
_AUTO_PANELS: Optional[List[dict]] = None
_AUTO_ERR:    Optional[str] = None
_AUTO_INFO:   Dict[str, Any] = {"device": "cpu", "gpu_name": None}
PANEL_DEVICE  = os.environ.get("PANEL_DEVICE", "auto").lower()
if PANEL_DEVICE not in ("auto", "cpu", "cuda"):
    PANEL_DEVICE = "auto"


def list_images() -> List[str]:
    exts = (".jpg", ".jpeg", ".png", ".webp", ".gif")
    files = [f for f in os.listdir(IMAGE_DIR) if f.lower().endswith(exts)]
    files.sort(key=numeric_sort_key)
    return files


def build_image_meta() -> None:
    global IMAGE_META, TOTAL_HEIGHT
    IMAGE_META = []
    offset = 0
    for name in list_images():
        path = IMAGE_DIR / name
        with Image.open(path) as im:
            w, h = im.size
        IMAGE_META.append({"name": name, "path": str(path), "width": w, "height": h, "offset_top": offset})
        offset += h
    TOTAL_HEIGHT = offset
    print(f"[INFO] Loaded {len(IMAGE_META)} images, total height={TOTAL_HEIGHT}px")


def crop_panel_across_images(panel_idx: int, top: float, bottom: float) -> str:
    top    = max(0, min(TOTAL_HEIGHT, int(round(top))))
    bottom = max(0, min(TOTAL_HEIGHT, int(round(bottom))))
    if bottom <= top:
        raise ValueError("Panel bottom must be greater than top")
    target_w = next(
        (m["width"] for m in IMAGE_META if top < m["offset_top"] + m["height"] and bottom > m["offset_top"]),
        0,
    )
    if target_w == 0:
        raise ValueError("Panel coordinates do not overlap with any image.")
    out_h   = bottom - top
    out_img = Image.new("RGB", (target_w, out_h), "white")
    y_cursor = 0
    for meta in IMAGE_META:
        img_top    = meta["offset_top"]
        img_bottom = img_top + meta["height"]
        overlap_top    = max(top, img_top)
        overlap_bottom = min(bottom, img_bottom)
        if overlap_bottom <= overlap_top:
            continue
        local_top = overlap_top - img_top
        local_bot = overlap_bottom - img_top
        crop_h    = local_bot - local_top
        with Image.open(meta["path"]) as im:
            im = im.convert("RGB")
            piece = im.crop((0, local_top, meta["width"], local_bot))
            if piece.width != target_w:
                piece = piece.resize((target_w, crop_h), Image.Resampling.LANCZOS)
        out_img.paste(piece, (0, y_cursor))
        y_cursor += crop_h
    out_name = f"chapter{_chapter}_{panel_idx + 1:03d}.png"
    out_img.save(PANEL_DIR / out_name)
    return out_name


@app.route("/")
def index() -> Response:
    storage_key = f"panel_marks_{_name}_{_chapter:02d}"
    min_panel   = load_gutter_config(_CONFIG_FILE).min_panel_height
    return render_template("editor.html", storage_key=storage_key, min_panel=min_panel)

@app.route("/images")
def images_list():
    return jsonify({"images": list_images()})

@app.route("/image/<path:name>")
def serve_image(name: str):
    return send_from_directory(IMAGE_DIR, name)

@app.route("/initial_panels")
def initial_panels():
    global _AUTO_PANELS, _AUTO_ERR, _AUTO_INFO
    if request.args.get("refresh") in ("1", "true", "yes"):
        _AUTO_PANELS = None
        _AUTO_ERR    = None
        _AUTO_INFO   = {"device": "cpu", "gpu_name": None}
    if _AUTO_PANELS is None and _AUTO_ERR is None:
        try:
            cfg = load_gutter_config(_CONFIG_FILE)
            panels, info = auto_detect_panels(IMAGE_DIR, cfg, sort_mode="numeric",
                                              device=PANEL_DEVICE, return_device_info=True)
            _AUTO_PANELS = panels
            _AUTO_INFO   = info
            gpu = f" ({info.get('gpu_name')})" if info.get("gpu_name") else ""
            print(f"[INFO] Auto-detected {len(_AUTO_PANELS)} panels (device={info.get('device')}{gpu}).")
        except Exception as exc:
            _AUTO_ERR = str(exc)
            print(f"[WARN] Auto-detect failed: {_AUTO_ERR}")
    if _AUTO_ERR:
        return jsonify({"status": "error", "message": _AUTO_ERR, "panels": [], **_AUTO_INFO})
    return jsonify({"status": "ok", "panels": _AUTO_PANELS or [], **_AUTO_INFO})

@app.route("/save_panels", methods=["POST"])
def save_panels():
    data = request.get_json(force=True)
    panels_data = data.get("panels", [])
    if not isinstance(panels_data, list):
        return jsonify({"status": "error", "message": "panels must be a list"}), 400
    saved = []
    for idx, p in enumerate(panels_data):
        if "top" not in p or "bottom" not in p:
            continue
        if float(p["bottom"]) <= float(p["top"]):
            continue
        saved.append(crop_panel_across_images(idx, float(p["top"]), float(p["bottom"])))
    return jsonify({"status": "ok", "saved": saved})

def main():
    if not IMAGE_DIR.exists() or not list_images():
        print(f"[ERROR] No images found in {IMAGE_DIR}")
        return
    build_image_meta()
    port = 5000
    print(f"[INFO] Starting webtoon panel editor at http://127.0.0.1:{port}/ (PANEL_DEVICE={PANEL_DEVICE})")
    run_app(app, port)


if __name__ == "__main__":
    main()
