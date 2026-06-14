"""mangaeasy.web.app.api_setup — prerequisite checks and one-click AI tool installs."""

from __future__ import annotations

import os
import threading

from flask import Blueprint, jsonify, request

from mangaeasy.web.app import jobs
from mangaeasy.web.app.state import action, lock, log, state

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
            finally:
                action("refresh-doctor")

        thread = threading.Thread(target=work, daemon=True)
        state["job"] = {"kind": "install", "name": name, "thread": thread, "proc": None}
        thread.start()
    return jsonify({"started": name})


@bp.route("/api/setup-gpu", methods=["POST"])
def api_setup_gpu():
    """Force-reinstall torch + torchvision with CUDA wheels into mangaeasy's own venv."""
    import sys
    from mangaeasy.tools.install import _has_gpu, _torch_index_url

    if not _has_gpu():
        return jsonify({"error": "No NVIDIA GPU detected"}), 400

    with lock:
        if jobs.job_running():
            return jsonify({"error": "another job is already running"}), 409

        index_url = _torch_index_url("cuda")
        python = sys.executable

        def work():
            from mangaeasy.tools.install import InstallError, _run
            try:
                log(f"[setup-gpu] Installing CUDA torch into mangaeasy env ({python})")
                log(f"[setup-gpu] Index: {index_url}")
                _run(
                    ["uv", "pip", "install",
                     "--python", python,
                     "torch", "torchvision",
                     "--index-url", index_url,
                     "--force-reinstall"],
                    log,
                )
                log("[setup-gpu] Done — restart mangaeasy to activate GPU acceleration.")
            except InstallError as exc:
                log(f"[setup-gpu] FAILED: {exc}")
            except Exception as exc:
                log(f"[setup-gpu] unexpected error: {exc}")
            finally:
                action("refresh-doctor")

        thread = threading.Thread(target=work, daemon=True)
        state["job"] = {"kind": "setup-gpu", "name": "cuda-torch", "thread": thread, "proc": None}
        thread.start()
    return jsonify({"started": True})


@bp.route("/api/install-whisper", methods=["POST"])
def api_install_whisper():
    """Install faster-whisper into its own managed isolated env (not the main venv)."""
    with lock:
        if jobs.job_running():
            return jsonify({"error": "another job is already running"}), 409

        def work():
            from mangaeasy.tools.install import InstallError, install_tool
            try:
                install_tool("faster-whisper", log=log)
            except InstallError as exc:
                log(f"[install-whisper] FAILED: {exc}")
            except Exception as exc:
                log(f"[install-whisper] unexpected error: {exc}")
            finally:
                action("refresh-doctor")

        thread = threading.Thread(target=work, daemon=True)
        state["job"] = {"kind": "install-whisper", "name": "faster-whisper", "thread": thread, "proc": None}
        thread.start()
    return jsonify({"started": True})


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
