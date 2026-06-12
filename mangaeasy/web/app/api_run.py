"""mangaeasy.web.app.api_run — run commands, track job status, manage editors."""

from __future__ import annotations

import threading

from flask import Blueprint, jsonify, request

from mangaeasy import __version__
from mangaeasy.web.app import jobs
from mangaeasy.web.app.state import lock, log, state

bp = Blueprint("run", __name__)

EDITOR_COMMANDS = (
    "cut-page",
    "panel-editor",
    "narration-editor",
    "narration-editor-all",
    "narration-review",
)


@bp.route("/api/run", methods=["POST"])
def api_run():
    from mangaeasy.cli import COMMANDS

    body = request.get_json(silent=True) or {}
    command = str(body.get("command", ""))
    args = [str(a) for a in body.get("args", [])]
    if command not in COMMANDS:
        return jsonify({"error": f"unknown command '{command}'"}), 400

    with lock:
        if jobs.job_running():
            return jsonify({"error": "another job is already running"}), 409
        proc = jobs.spawn_cli(command, args, state["project_root"])
        thread = threading.Thread(target=jobs.pump, args=(proc, command), daemon=True)
        state["job"] = {"kind": "run", "name": command, "thread": thread, "proc": proc}
        thread.start()
    return jsonify({"started": command})


@bp.route("/api/stop", methods=["POST"])
def api_stop():
    job = state["job"]
    if not job or not job["thread"].is_alive():
        return jsonify({"stopped": False, "reason": "no job running"})
    proc = job.get("proc")
    if proc is None:
        return jsonify({"stopped": False, "reason": "installs cannot be interrupted mid-step"}), 400
    proc.terminate()
    log(f"[app] stop requested for '{job['name']}'")
    return jsonify({"stopped": True})


@bp.route("/api/status")
def api_status():
    editors = {}
    for name, proc in list(state["editors"].items()):
        alive = proc.poll() is None
        if not alive:
            state["editors"].pop(name, None)
        editors[name] = alive
    return jsonify({
        "version": __version__,
        "project_root": str(state["project_root"]),
        "job": jobs.job_info(),
        "editors": editors,
    })


@bp.route("/api/editor/<name>/launch", methods=["POST"])
def api_editor_launch(name: str):
    if name not in EDITOR_COMMANDS:
        return jsonify({"error": f"unknown editor '{name}'"}), 404
    existing = state["editors"].get(name)
    if existing and existing.poll() is None:
        return jsonify({"running": True, "note": "already running"})
    proc = jobs.spawn_cli(name, [], state["project_root"])
    threading.Thread(target=jobs.pump, args=(proc, name), daemon=True).start()
    state["editors"][name] = proc
    return jsonify({"running": True})


@bp.route("/api/editor/<name>/stop", methods=["POST"])
def api_editor_stop(name: str):
    proc = state["editors"].get(name)
    if proc and proc.poll() is None:
        proc.terminate()
        log(f"[app] stopped editor '{name}'")
    state["editors"].pop(name, None)
    return jsonify({"running": False})
