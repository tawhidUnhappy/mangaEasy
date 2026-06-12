"""mangaeasy.web.app.api_project — project folder, config files, and UI state."""

from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, jsonify, request

from mangaeasy.web.app.state import ASSETS, log, save_app_state, state

bp = Blueprint("project", __name__)


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _example(name: str) -> dict:
    return _read_json(ASSETS / "config" / name) or {}


@bp.route("/api/project", methods=["GET", "POST"])
def api_project():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        raw = str(body.get("root", "")).strip()
        path = Path(raw).expanduser()
        if not raw or not path.is_dir():
            return jsonify({"error": f"not a folder: {raw}"}), 400
        state["project_root"] = path.resolve()
        log(f"[app] project folder set to {path.resolve()}")
        save_app_state()
    return jsonify({"root": str(state["project_root"])})


@bp.route("/api/appstate", methods=["GET", "POST"])
def api_appstate():
    """Remember UI field values (folders, selected step, ...) across launches."""
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        state["ui"].update(body)
        save_app_state()
    return jsonify({"ui": state["ui"]})


@bp.route("/api/config", methods=["GET", "POST"])
def api_config():
    root: Path = state["project_root"]
    cfg_path = root / "config.json"
    sys_path = root / "config.system.json"

    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        if "config" in body:
            cfg_path.write_text(json.dumps(body["config"], indent=2) + "\n", encoding="utf-8")
            log(f"[app] wrote {cfg_path}")
        if "system" in body:
            sys_path.write_text(json.dumps(body["system"], indent=2) + "\n", encoding="utf-8")
            log(f"[app] wrote {sys_path}")

    return jsonify({
        "root": str(root),
        "config": _read_json(cfg_path),
        "system": _read_json(sys_path),
        "config_example": _example("config.example.json"),
        "system_example": _example("config.system.example.json"),
    })
