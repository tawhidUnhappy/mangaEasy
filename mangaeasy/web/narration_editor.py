#!/usr/bin/env python3
"""mangaeasy.web.narration_editor — Flask UI for editing narration + Whisper transcription."""

import os
import tempfile
from pathlib import Path

from flask import jsonify, render_template, request, send_from_directory

from mangaeasy.config import load_download_config, load_system_config
from mangaeasy.narration import load_narration, save_narration
from mangaeasy.paths import narration_json as _narration_json, panels_dir
from mangaeasy.utils import numeric_sort_key
from mangaeasy.web.flask_utils import make_app, register_shutdown, run_app

os.environ.setdefault("OMP_NUM_THREADS", "1")

_dl      = load_download_config()
_chapter = int(_dl["chapter"])
_name    = str(_dl["name"])

NARRATION_JSON   = _narration_json(_name, _chapter)
IMAGE_SOURCE_DIR = panels_dir(_name, _chapter)

_w_cfg = load_system_config().get("whisper", {})
DEFAULT_MODEL   = os.environ.get("WHISPER_MODEL",   _w_cfg.get("model",   "medium"))
DEFAULT_DEVICE  = os.environ.get("WHISPER_DEVICE",  _w_cfg.get("device",  "auto")).lower().strip()
DEFAULT_COMPUTE = os.environ.get("WHISPER_COMPUTE", _w_cfg.get("compute", "float16"))

try:
    from faster_whisper import WhisperModel as _WhisperModel
    _WHISPER_AVAILABLE = True
except ImportError:
    _WhisperModel = None  # type: ignore[assignment]
    _WHISPER_AVAILABLE = False


app = make_app(__name__)
app.secret_key = "narration-editor-secret"
register_shutdown(app)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


try:
    NARRATIONS = load_narration(NARRATION_JSON)
except Exception as exc:
    print(f"[ERROR] Failed to load {NARRATION_JSON}: {exc}")
    NARRATIONS = []

# Auto-populate with all panels if file is missing or empty
if not NARRATIONS and IMAGE_SOURCE_DIR.exists():
    _images = sorted(
        [f.name for f in IMAGE_SOURCE_DIR.iterdir()
         if f.is_file() and f.suffix.lower() in _IMAGE_EXTS],
        key=numeric_sort_key,
    )
    NARRATIONS = [{"image": img, "narration": ""} for img in _images]
    if NARRATIONS:
        save_narration(NARRATIONS, NARRATION_JSON)
        print(f"[INFO] Created {NARRATION_JSON.name} with {len(NARRATIONS)} panels")

whisper_model        = None
WHISPER_ACTIVE_MODEL  = DEFAULT_MODEL
WHISPER_ACTIVE_DEVICE = None
WHISPER_ACTIVE_COMPUTE = None


def init_whisper_auto():
    global whisper_model, WHISPER_ACTIVE_DEVICE, WHISPER_ACTIVE_COMPUTE, WHISPER_ACTIVE_MODEL

    if not _WHISPER_AVAILABLE:
        print("[WARN] faster-whisper not installed — transcription disabled. "
              "Re-install mangaeasy with the whisper extra to enable it.")
        return

    def _try(device: str, compute: str) -> bool:
        global whisper_model
        try:
            whisper_model = _WhisperModel(DEFAULT_MODEL, device=device, compute_type=compute)
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


@app.route("/")
def root():
    return render_template("narration_editor.html")

@app.route("/api/state")
def api_state():
    total = len(NARRATIONS)
    if total == 0:
        return jsonify({"status": "ok", "finished": True, "total": 0, "index": 0, "item": None,
                        "whisper": {"model": WHISPER_ACTIVE_MODEL, "device": WHISPER_ACTIVE_DEVICE,
                                    "compute": WHISPER_ACTIVE_COMPUTE}})
    try:
        idx = max(0, min(int(request.args.get("i", "0")), total - 1))
    except ValueError:
        idx = 0
    item = NARRATIONS[idx]
    return jsonify({"status": "ok", "finished": False, "total": total, "index": idx,
                    "item": {"image": item.get("image", ""), "narration": item.get("narration", "")},
                    "whisper": {"model": WHISPER_ACTIVE_MODEL, "device": WHISPER_ACTIVE_DEVICE,
                                "compute": WHISPER_ACTIVE_COMPUTE}})

@app.route("/api/update_text", methods=["POST"])
def api_update_text():
    try:
        data = request.get_json(silent=True) or {}
        idx  = int(data.get("index"))
        text = str(data.get("text", ""))
        if 0 <= idx < len(NARRATIONS):
            NARRATIONS[idx]["narration"] = text
            save_narration(NARRATIONS, NARRATION_JSON)
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
        return jsonify({"status": "ok", "text": text,
                        "whisper": {"model": WHISPER_ACTIVE_MODEL, "device": WHISPER_ACTIVE_DEVICE,
                                    "compute": WHISPER_ACTIVE_COMPUTE}})
    except Exception as exc:
        return jsonify({"status": "error", "msg": str(exc)}), 500
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

@app.route("/images/<path:filename>")
def serve_image(filename: str):
    return send_from_directory(IMAGE_SOURCE_DIR, filename)

def main():
    port = int(load_system_config().get("ports", {}).get("narration_editor", 5003))
    print(f"[INFO] Narration editor at http://127.0.0.1:{port}/")
    print("[INFO] Shortcut: INSERT => start/stop mic")
    run_app(app, port)


if __name__ == "__main__":
    main()
