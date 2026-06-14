"""mangaeasy.web.app.api_run — run commands, track job status, manage editors."""

from __future__ import annotations

import json
import threading

from flask import Blueprint, jsonify, request

from mangaeasy import __version__
from mangaeasy.web.app import jobs
from mangaeasy.web.app.state import lock, log, state
from mangaeasy.web.flask_utils import terminal_broadcaster

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


@bp.route("/api/run-chain", methods=["POST"])
def api_run_chain():
    """Run several commands back to back as one job, stopping on first failure.

    Body: {"steps": [{"command": ..., "args": [...]}, ...]}.
    """
    from mangaeasy.cli import COMMANDS

    body = request.get_json(silent=True) or {}
    steps: list[tuple[str, list[str]]] = []
    for raw in body.get("steps") or []:
        command = str(raw.get("command", ""))
        if command not in COMMANDS:
            return jsonify({"error": f"unknown command '{command}'"}), 400
        steps.append((command, [str(a) for a in raw.get("args", [])]))
    if not steps:
        return jsonify({"error": "no steps given"}), 400

    label = " → ".join(command for command, _ in steps)
    with lock:
        if jobs.job_running():
            return jsonify({"error": "another job is already running"}), 409

        job: dict = {"kind": "run", "name": label, "thread": None, "proc": None}

        def work():
            for command, args in steps:
                proc = jobs.spawn_cli(command, args, state["project_root"])
                job["proc"] = proc  # so /api/stop terminates the current step
                assert proc.stdout is not None
                while True:
                    chunk = proc.stdout.read(512)
                    if not chunk:
                        break
                    terminal_broadcaster.write_raw(chunk)
                code = proc.wait()
                color = "\x1b[32m" if code == 0 else "\x1b[31m"
                log(f"{color}[{command}] finished (exit {code})\x1b[0m")
                if code != 0:
                    log(f"\x1b[31m[workflow] stopped — '{command}' failed; later steps skipped.\x1b[0m")
                    return
            log("\x1b[32m[workflow] all steps finished ✓\x1b[0m")

        thread = threading.Thread(target=work, daemon=True)
        job["thread"] = thread
        state["job"] = job
        thread.start()
    return jsonify({"started": label})


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


def _open_editor_window(name: str, url: str) -> None:
    """Open *url* in a native pywebview window, or the OS browser as fallback."""
    window = state.get("window")
    if window is None:
        import webbrowser
        webbrowser.open(url)
        return
    try:
        # openEditorTab is exposed on window by editors.js; it adds a tab
        # in the topbar and loads the editor in an iframe inside the app.
        window.evaluate_js(
            f"window.openEditorTab({json.dumps(name)}, {json.dumps(url)})"
        )
    except Exception as exc:
        log(f"[app] editor tab: {exc}")
        import webbrowser
        webbrowser.open(url)


def _pump_editor(proc, name: str) -> None:
    """Like jobs.pump but intercepts MANGAEASY_OPEN_URL: lines."""
    assert proc.stdout is not None
    opened = False
    from mangaeasy.web.app.jobs import iter_lines
    for line in iter_lines(proc.stdout):
        if line.startswith("MANGAEASY_OPEN_URL:"):
            url = line[len("MANGAEASY_OPEN_URL:"):]
            state["editor_urls"][name] = url
            log(f"[{name}] ready")
            if not opened:
                _open_editor_window(name, url)
                opened = True
        else:
            log(line)
    code = proc.wait()
    log(f"[{name}] editor closed (exit {code})")
    state["editors"].pop(name, None)
    state["editor_urls"].pop(name, None)


@bp.route("/api/editor/<name>/launch", methods=["POST"])
def api_editor_launch(name: str):
    if name not in EDITOR_COMMANDS:
        return jsonify({"error": f"unknown editor '{name}'"}), 404
    existing = state["editors"].get(name)
    if existing and existing.poll() is None:
        # Already running — re-open its window if we know the URL.
        url = state["editor_urls"].get(name)
        if url:
            _open_editor_window(name, url)
        return jsonify({"running": True, "note": "already running"})
    proc = jobs.spawn_cli(name, [], state["project_root"])
    threading.Thread(target=_pump_editor, args=(proc, name), daemon=True).start()
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
