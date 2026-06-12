#!/usr/bin/env python3
"""mangaeasy.web.cut_page — Flask UI for manga/manhua panel box cropper.

Reading direction is configured in config.system.json → cut_page.reading_direction:
  "rtl"  — manga   (Japan):  panels read right-to-left, top-to-bottom
  "ltr"  — manhua  (China):  panels read left-to-right, top-to-bottom
  (vertical manhwa/Korea uses mangaeasy panel-editor instead of this tool)
"""

import json
import os
import re
from pathlib import Path

from flask import jsonify, render_template, request, send_from_directory, Response, stream_with_context
from PIL import Image

from mangaeasy.config import PROJECT_ROOT, load_download_config, load_system_config
from mangaeasy.panels.ai import detect_panels_ai
from mangaeasy.paths import chapter_dir
from mangaeasy.utils import numeric_sort_key
from mangaeasy.web.flask_utils import LogBroadcaster, make_app, register_shutdown, run_app

Image.MAX_IMAGE_PIXELS = None

# ── Live log capture ──────────────────────────────────────────────────────────
_broadcaster = LogBroadcaster()
_broadcaster.install()


# ── Core logic ────────────────────────────────────────────────────────────────

class MangaCropperCore:
    def __init__(self):
        dl = load_download_config()
        self.manga_name = str(dl.get("name", "unknown"))
        self.chapter    = int(dl.get("chapter", 1))
        self.image_dir  = chapter_dir(self.manga_name, self.chapter) / "download"
        self.panel_dir  = chapter_dir(self.manga_name, self.chapter) / "panels"
        self.state_file = self.panel_dir / "boxes_state.json"
        self.panel_dir.mkdir(parents=True, exist_ok=True)

    def get_image_list(self):
        exts = (".jpg", ".jpeg", ".png", ".webp", ".gif")
        if not self.image_dir.exists():
            return []
        files = [f for f in os.listdir(self.image_dir) if f.lower().endswith(exts)]
        return sorted(files, key=numeric_sort_key)

    def is_safe_path(self, filename: str) -> bool:
        target = (self.image_dir / filename).resolve()
        return str(target).startswith(str(self.image_dir.resolve()))

    def read_state(self):
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def write_state(self, state: dict):
        with open(self.state_file, "w") as f:
            json.dump(state, f)

    def crop_and_save_all(self, progress: dict) -> int:
        saved = 0
        all_images = self.get_image_list()
        for img_name in sorted(progress.keys(), key=numeric_sort_key):
            boxes = progress[img_name]
            if not boxes:
                continue
            img_path = self.image_dir / img_name
            if not img_path.exists() or not self.is_safe_path(img_name):
                continue
            base_name = Path(img_name).stem
            nums = re.findall(r"\d+", base_name)
            if nums:
                page_number = int(nums[-1])
            else:
                try:
                    page_number = all_images.index(img_name) + 1
                except ValueError:
                    page_number = 99
            with Image.open(img_path) as im:
                im = im.convert("RGB")
                for idx, box in enumerate(boxes):
                    x1, x2 = sorted([int(box["x1"]), int(box["x2"])])
                    y1, y2 = sorted([int(box["y1"]), int(box["y2"])])
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(im.width, x2), min(im.height, y2)
                    if x2 <= x1 or y2 <= y1:
                        continue
                    piece = im.crop((x1, y1, x2, y2))
                    raw_num = box.get("panelNum")
                    panel_num = int(raw_num) if raw_num else idx + 1
                    out_name = f"{self.chapter:02d}_{page_number:02d}_{panel_num:02d}.png"
                    piece.save(self.panel_dir / out_name)
                    saved += 1
        return saved


# ── Flask app ─────────────────────────────────────────────────────────────────

app  = make_app(__name__)
core = MangaCropperCore()

register_shutdown(app)
_broadcaster.register_route(app)


@app.route("/")
def index():
    return render_template("cut_page.html", manga=core.manga_name, chapter=core.chapter)

@app.route("/images")
def images_list():
    return jsonify({"images": core.get_image_list()})

@app.route("/image/<path:name>")
def serve_image(name):
    if not core.is_safe_path(name):
        return "Access Denied", 403
    return send_from_directory(core.image_dir, name)

@app.route("/load_progress")
def load_progress():
    return jsonify({"status": "ok", "progress": core.read_state()})

@app.route("/save_progress", methods=["POST"])
def save_progress():
    data = request.get_json(force=True)
    progress = core.read_state()
    progress[data.get("image_name")] = data.get("boxes", [])
    core.write_state(progress)
    return jsonify({"status": "ok"})

@app.route("/api/gpu_info")
def api_gpu_info():
    import torch
    if not torch.cuda.is_available():
        return jsonify({"cuda": False})
    props = torch.cuda.get_device_properties(0)
    total_mb = props.total_memory // 1024 // 1024
    used_mb  = torch.cuda.memory_allocated(0) // 1024 // 1024
    return jsonify({"cuda": True, "name": props.name, "total_mb": total_mb,
                    "used_mb": used_mb, "free_mb": total_mb - used_mb,
                    "torch": __import__("torch").__version__})

@app.route("/auto_detect/<path:name>")
def auto_detect(name):
    if not core.is_safe_path(name):
        return "Access Denied", 403
    img_path = core.image_dir / name
    if not img_path.exists():
        return jsonify({"status": "error", "message": "Image not found"}), 404
    try:
        boxes  = detect_panels_ai(img_path)
        source = "magi_ai" if boxes else "none"
    except Exception as exc:
        print(f"[auto_detect] MAGI error: {exc}")
        boxes, source = [], "error"
    return jsonify({"status": "ok", "boxes": boxes, "source": source, "panels_found": len(boxes)})

@app.route("/auto_detect_all")
def auto_detect_all():
    def generate():
        images   = core.get_image_list()
        total    = len(images)
        progress = core.read_state()
        for i, name in enumerate(images):
            existing = progress.get(name, [])
            if existing:
                yield "data: " + json.dumps({"page": name, "index": i, "total": total,
                                              "boxes": existing, "panels_found": len(existing),
                                              "source": "cached"}) + "\n\n"
                continue
            img_path = core.image_dir / name
            try:
                boxes  = detect_panels_ai(img_path)
                source = "magi_ai" if boxes else "none"
            except Exception as exc:
                print(f"[auto_detect_all] {name}: {exc}")
                boxes, source = [], "error"
            progress[name] = boxes
            core.write_state(progress)
            yield "data: " + json.dumps({"page": name, "index": i, "total": total,
                                          "boxes": boxes, "panels_found": len(boxes),
                                          "source": source}) + "\n\n"
        yield "data: " + json.dumps({"done": True, "total": total}) + "\n\n"
    return Response(stream_with_context(generate()),
                    content_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/crop_all", methods=["POST"])
def crop_all():
    progress = core.read_state()
    if not progress:
        return jsonify({"status": "error", "message": "No progress saved."}), 400
    try:
        saved = core.crop_and_save_all(progress)
        return jsonify({"status": "ok", "saved_count": saved})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500

@app.route("/api/config")
def api_config():
    syscfg = load_system_config()
    cp_cfg = syscfg.get("cut_page", {})
    return jsonify({"reading_direction": cp_cfg.get("reading_direction", "rtl")})


def main():
    if not core.image_dir.exists() or not core.get_image_list():
        print(f"[ERROR] Image directory not found or empty: {core.image_dir}")
        return
    port = int(load_system_config().get("ports", {}).get("cut_page", 5000))
    print(f"[INFO] Starting cut-page editor at http://127.0.0.1:{port}/")
    run_app(app, port)


if __name__ == "__main__":
    main()
