"""mangaeasy.web.app.api_fs — folder/file pickers and filesystem helpers.

The Browse… buttons first try the native OS dialog (desktop window). When the
app runs in a plain browser there is no native dialog, so the UI falls back
to an in-app picker backed by /api/fs/list.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

from mangaeasy.web.app.state import (
    log,
    relative_to_project,
    resolve_against_project,
    state,
)

bp = Blueprint("fs", __name__)


@bp.route("/api/pick-folder", methods=["POST"])
def api_pick_folder():
    """Open the native OS folder dialog (desktop window only).

    Returns {"folder": path} on selection, {"folder": null} on cancel, and
    {"unsupported": true} in browser mode.
    """
    win = state.get("window")
    if win is None:
        return jsonify({"unsupported": True})
    try:
        import webview

        body = request.get_json(silent=True) or {}
        start = resolve_against_project(str(body.get("start") or ""))
        directory = str(start) if start.is_dir() else str(Path.home())
        result = win.create_file_dialog(webview.FOLDER_DIALOG, directory=directory)
        if not result:
            return jsonify({"folder": None})
        folder = result[0] if isinstance(result, (list, tuple)) else result
        return jsonify({"folder": str(folder)})
    except Exception as exc:
        log(f"[app] native folder dialog failed: {exc}")
        return jsonify({"unsupported": True})


@bp.route("/api/pick-file", methods=["POST"])
def api_pick_file():
    """Open the native OS file dialog (desktop window only).

    Body: {"start": path, "file_types": ["Audio files (*.mp3;*.wav)", ...]}.
    Returns {"file", "relative"} on selection, {"file": null} on cancel, and
    {"unsupported": true} in browser mode.
    """
    win = state.get("window")
    if win is None:
        return jsonify({"unsupported": True})
    try:
        import webview

        body = request.get_json(silent=True) or {}
        start = resolve_against_project(str(body.get("start") or ""))
        if start.is_dir():
            directory = str(start)
        elif start.parent.is_dir():
            directory = str(start.parent)
        else:
            directory = str(Path.home())
        kwargs: dict = {"directory": directory}
        file_types = body.get("file_types")
        if file_types:
            kwargs["file_types"] = tuple(file_types)
        result = win.create_file_dialog(webview.OPEN_DIALOG, **kwargs)
        if not result:
            return jsonify({"file": None})
        chosen = Path(result[0] if isinstance(result, (list, tuple)) else result)
        return jsonify({"file": str(chosen), "relative": relative_to_project(chosen)})
    except Exception as exc:
        log(f"[app] native file dialog failed: {exc}")
        return jsonify({"unsupported": True})


def _list_drives() -> list[str]:
    if os.name != "nt":
        return []
    import string

    return [f"{letter}:\\" for letter in string.ascii_uppercase
            if Path(f"{letter}:\\").exists()]


@bp.route("/api/fs/list")
def api_fs_list():
    """List subfolders (and optionally files) of a path — backs the in-app picker.

    Query params: path, files=1 to include files, exts=mp3,wav to filter them.
    """
    raw = (request.args.get("path") or "").strip()
    want_files = request.args.get("files") == "1"
    exts = {e.strip().lower() for e in (request.args.get("exts") or "").split(",") if e.strip()}
    path = resolve_against_project(raw) if raw else Path.home()
    try:
        path = path.resolve()
        if not path.is_dir():
            return jsonify({"error": f"not a folder: {raw}"}), 400
        dirs, files = [], []
        for entry in path.iterdir():
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                dirs.append(entry.name)
            elif want_files and (not exts or entry.suffix.lower().lstrip(".") in exts):
                files.append(entry.name)
        dirs.sort(key=str.lower)
        files.sort(key=str.lower)
    except PermissionError:
        return jsonify({"error": f"no permission to read {path}"}), 403
    except OSError as exc:
        return jsonify({"error": str(exc)}), 400
    parent = str(path.parent) if path.parent != path else None
    return jsonify({
        "path": str(path),
        "parent": parent,
        "dirs": dirs,
        "files": files,
        "drives": _list_drives(),
        "home": str(Path.home()),
    })


@bp.route("/api/open-folder", methods=["POST"])
def api_open_folder():
    """Open a folder in the system file manager (Explorer / Finder / ...)."""
    body = request.get_json(silent=True) or {}
    raw = str(body.get("path", "")).strip()
    if not raw:
        return jsonify({"error": "no folder given"}), 400
    path = resolve_against_project(raw)
    if not path.is_dir():
        return jsonify({"error": f"folder does not exist yet: {path}"}), 400
    path = path.resolve()
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606 — local desktop convenience
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        return jsonify({"error": f"could not open folder: {exc}"}), 500
    return jsonify({"opened": str(path)})
