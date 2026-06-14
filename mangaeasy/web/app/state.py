"""mangaeasy.web.app.state — shared state for the control center.

One process-wide dict (``state``) holds the project root, the running job,
the editor processes, and the pywebview window handle. UI choices and the
project root persist across launches in ``MANGAEASY_HOME/app_state.json``.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from mangaeasy.tools.external import mangaeasy_home
from mangaeasy.web.flask_utils import LogBroadcaster

ASSETS = Path(__file__).resolve().parents[2] / "assets"
APP_STATE_FILE = mangaeasy_home() / "app_state.json"

broadcaster = LogBroadcaster(buf_size=400)


def log(line: str) -> None:
    broadcaster.broadcast(line)


def action(name: str) -> None:
    """Push a control action to all live SSE clients (e.g. 'refresh-doctor')."""
    broadcaster.broadcast_action(name)


def _load_app_state() -> dict:
    try:
        data = json.loads(APP_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _initial_project_root(saved: dict) -> Path:
    """Prefer the current directory when it looks like a project; otherwise
    fall back to the project folder used last time the app ran."""
    cwd = Path.cwd().resolve()
    if (cwd / "config.json").exists() or (cwd / "content").is_dir():
        return cwd
    remembered = saved.get("project_root")
    if remembered:
        path = Path(remembered)
        if path.is_dir():
            return path.resolve()
    return cwd


_saved_state = _load_app_state()

lock = threading.Lock()
state: dict = {
    "project_root": _initial_project_root(_saved_state),
    "ui": dict(_saved_state.get("ui") or {}),  # free-form UI field values
    "window": None,        # pywebview window when running as a desktop app
    "job": None,           # {"kind", "name", "thread", "proc"}
    "editors": {},         # command name -> subprocess.Popen
    "editor_urls": {},     # command name -> URL once the editor is ready
}


def save_app_state() -> None:
    try:
        APP_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {"project_root": str(state["project_root"]), "ui": state["ui"]}
        APP_STATE_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        log(f"[app] could not save app state: {exc}")


def resolve_against_project(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = state["project_root"] / path
    return path


def relative_to_project(path: Path) -> str | None:
    """Project-relative form of *path* (forward slashes), or None if outside."""
    try:
        return path.resolve().relative_to(state["project_root"]).as_posix()
    except ValueError:
        return None
