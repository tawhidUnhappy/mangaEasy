"""mangaeasy.web.app — the mangaEasy control center.

`mangaeasy app` opens a desktop window (pywebview) wrapping a local Flask UI.
If pywebview or a GUI backend is unavailable, it falls back to the browser.

The UI drives everything end-to-end without the terminal:
  Setup   — prerequisite checks + one-click install of external AI tools
  Project — pick the project folder, edit config.json / config.system.json
  Run     — run the video pipeline or chapter commands with live logs
  Editors — launch the panel / narration web editors

All endpoints bind to 127.0.0.1 only.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from mangaeasy import __version__
from mangaeasy.web.flask_utils import LogBroadcaster, register_shutdown

ASSETS = Path(__file__).resolve().parents[1] / "assets"
DEFAULT_PORT = 5010

EDITOR_COMMANDS = (
    "cut-page",
    "panel-editor",
    "narration-editor",
    "narration-editor-all",
    "narration-review",
)

app = Flask(
    __name__,
    # Always the packaged assets — the control center must not be shadowed by a
    # project-local templates/ folder the way the editors intentionally are.
    template_folder=str(ASSETS / "templates"),
    static_folder=str(ASSETS / "static"),
)
register_shutdown(app)

broadcaster = LogBroadcaster(buf_size=400)
broadcaster.register_route(app)

_lock = threading.Lock()
_state: dict = {
    "project_root": Path.cwd().resolve(),
    "job": None,      # {"kind", "name", "thread", "proc"}
    "editors": {},    # command name -> subprocess.Popen
}


def _log(line: str) -> None:
    broadcaster.broadcast(line)


# ── Job helpers ───────────────────────────────────────────────────────────────


def _job_running() -> bool:
    job = _state["job"]
    return bool(job and job["thread"].is_alive())


def _job_info() -> dict | None:
    job = _state["job"]
    if not job:
        return None
    return {"kind": job["kind"], "name": job["name"], "running": job["thread"].is_alive()}


def _spawn_cli(command: str, args: list[str], cwd: Path) -> subprocess.Popen:
    env = dict(os.environ)
    env["MANGAEASY_PROJECT_ROOT"] = str(cwd)
    env.setdefault("PYTHONUNBUFFERED", "1")
    full = [sys.executable, "-m", "mangaeasy.cli", command, *args]
    _log(f"$ mangaeasy {command} {' '.join(args)}")
    return subprocess.Popen(
        full,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def _pump(proc: subprocess.Popen, label: str) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        _log(line.rstrip("\n"))
    code = proc.wait()
    _log(f"[{label}] finished with exit code {code}")


# ── Pages ─────────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    return render_template("app.html", version=__version__)


# ── Setup API ─────────────────────────────────────────────────────────────────


@app.route("/api/doctor")
def api_doctor():
    from mangaeasy.tools.install import doctor

    return jsonify(doctor())


@app.route("/api/install-tool/<name>", methods=["POST"])
def api_install_tool(name: str):
    from mangaeasy.tools.install import TOOLS, InstallError, install_tool

    if name not in TOOLS:
        return jsonify({"error": f"unknown tool '{name}'"}), 404
    with _lock:
        if _job_running():
            return jsonify({"error": "another job is already running"}), 409

        body = request.get_json(silent=True) or {}

        def work():
            try:
                install_tool(
                    name,
                    gpu="cpu" if body.get("cpu") else "auto",
                    skip_model=bool(body.get("skip_model")),
                    log=_log,
                )
            except InstallError as exc:
                _log(f"[install-tool] FAILED: {exc}")
            except Exception as exc:  # keep the app alive whatever happens
                _log(f"[install-tool] unexpected error: {exc}")

        thread = threading.Thread(target=work, daemon=True)
        _state["job"] = {"kind": "install", "name": name, "thread": thread, "proc": None}
        thread.start()
    return jsonify({"started": name})


# ── Project / config API ──────────────────────────────────────────────────────


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _example(name: str) -> dict:
    return _read_json(ASSETS / "config" / name) or {}


@app.route("/api/project", methods=["GET", "POST"])
def api_project():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        raw = str(body.get("root", "")).strip()
        path = Path(raw).expanduser()
        if not raw or not path.is_dir():
            return jsonify({"error": f"not a folder: {raw}"}), 400
        _state["project_root"] = path.resolve()
        _log(f"[app] project folder set to {path.resolve()}")
    return jsonify({"root": str(_state["project_root"])})


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    root: Path = _state["project_root"]
    cfg_path = root / "config.json"
    sys_path = root / "config.system.json"

    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        if "config" in body:
            cfg_path.write_text(json.dumps(body["config"], indent=2) + "\n", encoding="utf-8")
            _log(f"[app] wrote {cfg_path}")
        if "system" in body:
            sys_path.write_text(json.dumps(body["system"], indent=2) + "\n", encoding="utf-8")
            _log(f"[app] wrote {sys_path}")

    return jsonify({
        "root": str(root),
        "config": _read_json(cfg_path),
        "system": _read_json(sys_path),
        "config_example": _example("config.example.json"),
        "system_example": _example("config.system.example.json"),
    })


# ── Run API ───────────────────────────────────────────────────────────────────


@app.route("/api/run", methods=["POST"])
def api_run():
    from mangaeasy.cli import COMMANDS

    body = request.get_json(silent=True) or {}
    command = str(body.get("command", ""))
    args = [str(a) for a in body.get("args", [])]
    if command not in COMMANDS:
        return jsonify({"error": f"unknown command '{command}'"}), 400

    with _lock:
        if _job_running():
            return jsonify({"error": "another job is already running"}), 409
        proc = _spawn_cli(command, args, _state["project_root"])
        thread = threading.Thread(target=_pump, args=(proc, command), daemon=True)
        _state["job"] = {"kind": "run", "name": command, "thread": thread, "proc": proc}
        thread.start()
    return jsonify({"started": command})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    job = _state["job"]
    if not job or not job["thread"].is_alive():
        return jsonify({"stopped": False, "reason": "no job running"})
    proc = job.get("proc")
    if proc is None:
        return jsonify({"stopped": False, "reason": "installs cannot be interrupted mid-step"}), 400
    proc.terminate()
    _log(f"[app] stop requested for '{job['name']}'")
    return jsonify({"stopped": True})


@app.route("/api/status")
def api_status():
    editors = {}
    for name, proc in list(_state["editors"].items()):
        alive = proc.poll() is None
        if not alive:
            _state["editors"].pop(name, None)
        editors[name] = alive
    return jsonify({
        "version": __version__,
        "project_root": str(_state["project_root"]),
        "job": _job_info(),
        "editors": editors,
    })


# ── Editors API ───────────────────────────────────────────────────────────────


@app.route("/api/editor/<name>/launch", methods=["POST"])
def api_editor_launch(name: str):
    if name not in EDITOR_COMMANDS:
        return jsonify({"error": f"unknown editor '{name}'"}), 404
    existing = _state["editors"].get(name)
    if existing and existing.poll() is None:
        return jsonify({"running": True, "note": "already running"})
    proc = _spawn_cli(name, [], _state["project_root"])
    threading.Thread(target=_pump, args=(proc, name), daemon=True).start()
    _state["editors"][name] = proc
    return jsonify({"running": True})


@app.route("/api/editor/<name>/stop", methods=["POST"])
def api_editor_stop(name: str):
    proc = _state["editors"].get(name)
    if proc and proc.poll() is None:
        proc.terminate()
        _log(f"[app] stopped editor '{name}'")
    _state["editors"].pop(name, None)
    return jsonify({"running": False})


# ── Entry point ───────────────────────────────────────────────────────────────


def _wait_for_port(port: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _cleanup() -> None:
    job = _state["job"]
    if job and job.get("proc") and job["proc"].poll() is None:
        job["proc"].terminate()
    for proc in _state["editors"].values():
        if proc.poll() is None:
            proc.terminate()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="mangaeasy app", description="Open the mangaEasy control center."
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--browser", action="store_true",
                        help="Open in the default browser instead of a desktop window.")
    args = parser.parse_args()

    url = f"http://127.0.0.1:{args.port}"

    window = None
    if not args.browser:
        try:
            import webview  # pywebview — lazy so headless installs still work
            window = webview
        except Exception as exc:
            print(f"[app] desktop window unavailable ({exc}); falling back to browser.")

    if window is None:
        from mangaeasy.web.flask_utils import run_app

        print(f"[app] control center: {url}")
        try:
            run_app(app, args.port)
        finally:
            _cleanup()
        return 0

    server = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=args.port, debug=False,
                               use_reloader=False, threaded=True),
        daemon=True,
    )
    server.start()
    if not _wait_for_port(args.port):
        print("[app] server did not start in time.")
        return 1

    window.create_window("mangaEasy", url, width=1240, height=820, min_size=(900, 600))
    window.start()
    _cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
