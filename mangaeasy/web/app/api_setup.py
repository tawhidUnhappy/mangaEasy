"""mangaeasy.web.app.api_setup — prerequisite checks and one-click AI tool installs."""

from __future__ import annotations

import os
import threading

from flask import Blueprint, jsonify, request

from mangaeasy.web.app import jobs
from mangaeasy.web.app.state import lock, log, state

bp = Blueprint("setup", __name__)


@bp.route("/api/doctor")
def api_doctor():
    from mangaeasy.tools.install import doctor

    return jsonify(doctor())


@bp.route("/api/install-tool/<name>", methods=["POST"])
def api_install_tool(name: str):
    from mangaeasy.tools.install import TOOLS, InstallError, install_tool

    if name not in TOOLS:
        return jsonify({"error": f"unknown tool '{name}'"}), 404
    with lock:
        if jobs.job_running():
            return jsonify({"error": "another job is already running"}), 409

        body = request.get_json(silent=True) or {}

        def work():
            try:
                install_tool(
                    name,
                    gpu="cpu" if body.get("cpu") else "auto",
                    skip_model=bool(body.get("skip_model")),
                    log=log,
                )
            except InstallError as exc:
                log(f"[install-tool] FAILED: {exc}")
            except Exception as exc:  # keep the app alive whatever happens
                log(f"[install-tool] unexpected error: {exc}")

        thread = threading.Thread(target=work, daemon=True)
        state["job"] = {"kind": "install", "name": name, "thread": thread, "proc": None}
        thread.start()
    return jsonify({"started": name})


@bp.route("/api/install-tool/<name>", methods=["DELETE"])
def api_delete_tool(name: str):
    import shutil
    from mangaeasy.tools.install import TOOLS
    from mangaeasy.tools.external import resolve_tool_dir, tools_home

    if name not in TOOLS:
        return jsonify({"error": f"unknown tool '{name}'"}), 404
    if jobs.job_running():
        return jsonify({"error": "cannot delete while a job is running"}), 409

    path = resolve_tool_dir(name, required=False)
    if path is None:
        return jsonify({"deleted": False, "reason": "not installed"})

    # Safety: refuse to delete anything outside the managed tools directory
    try:
        path.relative_to(tools_home())
    except ValueError:
        return jsonify({"error": "path is outside the managed tools directory"}), 400

    def _force_remove(func, fpath, _exc):
        # Git repos on Windows leave object files read-only; clear the bit and retry.
        import stat
        try:
            os.chmod(fpath, stat.S_IWRITE)
            func(fpath)
        except Exception:
            pass

    try:
        shutil.rmtree(path, onerror=_force_remove)
        log(f"[setup] deleted tool '{name}' ({path})")
        return jsonify({"deleted": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
