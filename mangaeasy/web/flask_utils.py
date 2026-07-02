"""mangaeasy.web.flask_utils — shared Flask app factory and utilities.

Every web tool in mangaeasy uses the same project-root template/static
folders, the same server startup pattern, and the same /shutdown route.
This module centralises all of that so individual tools stay thin.

Usage:
    from mangaeasy.web.flask_utils import make_app, run_app, register_shutdown

    app = make_app()

    register_shutdown(app)        # adds POST /shutdown

    def main():
        run_app(app, port=5001)   # opens browser + starts Flask
"""

from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import webbrowser
from collections import deque
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, stream_with_context

from mangaeasy.config import PROJECT_ROOT


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def _asset_dir(name: str) -> Path:
    project_dir = PROJECT_ROOT / name
    if project_dir.exists():
        return project_dir
    return Path(__file__).resolve().parents[1] / "assets" / name


def make_app(import_name: str = __name__, **kwargs) -> Flask:
    """Return a Flask app with project-standard template and static folders."""
    return Flask(
        import_name,
        template_folder=str(_asset_dir("templates")),
        static_folder=str(_asset_dir("static")),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Standard server startup
# ---------------------------------------------------------------------------

def run_app(app: Flask, port: int, url_path: str = "/") -> None:
    """Start the Flask dev server and open the UI.

    When launched from the desktop app (MANGAEASY_APP_MODE=1) the parent
    process reads the stdout signal and opens the URL in a native webview
    window.  Otherwise the system browser is used.
    """
    url = f"http://127.0.0.1:{port}{url_path}"
    if os.environ.get("MANGAEASY_APP_MODE"):
        # Signal to the parent desktop process — it will open the URL in a
        # new pywebview window instead of the OS browser.
        threading.Timer(1.0, lambda: print(f"MANGAEASY_OPEN_URL:{url}", flush=True)).start()
    else:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)


# ---------------------------------------------------------------------------
# Standard shutdown route
# ---------------------------------------------------------------------------

def register_shutdown(app: Flask) -> None:
    """Register POST /shutdown on *app* — exits the process after 1 s."""

    @app.route("/shutdown", methods=["POST"])
    def _shutdown():
        from flask import jsonify
        threading.Timer(1.0, lambda: os._exit(0)).start()
        return jsonify({"status": "ok", "message": "Shutting down..."})


# ---------------------------------------------------------------------------
# Live log broadcaster (SSE) — opt-in, used by cut_page
# ---------------------------------------------------------------------------

_LOG_SKIP = re.compile(
    r"^\s*$"
    r"|GET /log_stream"
    r"|GET /image/"
    r"|GET /static/"
    r'|HTTP/1\.[01]" 200 -\s*$'
)


class LogBroadcaster:
    """Captures stdout/stderr and broadcasts lines to SSE clients.

    Usage:
        broadcaster = LogBroadcaster()
        broadcaster.install()              # redirect stdout + stderr
        broadcaster.register_route(app)    # adds GET /log_stream
    """

    def __init__(self, buf_size: int = 150):
        self._buf:     deque         = deque(maxlen=buf_size)
        self._clients: list          = []
        self._lock:    threading.Lock = threading.Lock()

    def broadcast(self, text: str) -> None:
        text = text.rstrip()
        if not text or _LOG_SKIP.search(text):
            return
        ts    = datetime.now().strftime("%H:%M:%S")
        entry = json.dumps({"ts": ts, "msg": text})
        with self._lock:
            self._buf.append(entry)
            dead = []
            for q in self._clients:
                try:
                    q.put_nowait(entry)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._clients.remove(q)

    def broadcast_action(self, action: str) -> None:
        """Send a control signal to all SSE clients (not buffered in history)."""
        entry = json.dumps({"action": action})
        with self._lock:
            for q in self._clients:
                try:
                    q.put_nowait(entry)
                except queue.Full:
                    pass

    def broadcast_progress(self, value: int, total: int, label: str = "") -> None:
        """Send a progress update to all SSE clients (not buffered in history).

        value=0, total=0 → indeterminate animated bar.
        value/total > 0  → determinate fill.
        value == total   → 100 % complete.
        """
        entry = json.dumps({"progress": {"value": value, "total": total, "label": label}})
        with self._lock:
            for q in self._clients:
                try:
                    q.put_nowait(entry)
                except queue.Full:
                    pass

    def install(self) -> None:
        """Redirect sys.stdout and sys.stderr through this broadcaster."""
        broadcaster = self

        class _Tee:
            def __init__(self, orig):
                self._orig = orig

            def write(self, text: str):
                self._orig.write(text)
                broadcaster.broadcast(text)

            def flush(self):
                self._orig.flush()

            def __getattr__(self, name):
                return getattr(self._orig, name)

        sys.stdout = _Tee(sys.stdout)
        sys.stderr = _Tee(sys.stderr)

    def register_route(self, app: Flask) -> None:
        """Add GET /log_stream (SSE) to *app*."""
        broadcaster = self

        @app.route("/log_stream")
        def _log_stream():
            client_q: queue.Queue = queue.Queue(maxsize=300)
            with broadcaster._lock:
                broadcaster._clients.append(client_q)
                buffered = list(broadcaster._buf)
            for entry in buffered:
                try:
                    client_q.put_nowait(entry)
                except queue.Full:
                    break

            def generate():
                try:
                    while True:
                        try:
                            entry = client_q.get(timeout=5.0)
                            yield f"data: {entry}\n\n"
                        except queue.Empty:
                            yield 'data: {"ping":true}\n\n'
                finally:
                    with broadcaster._lock:
                        try:
                            broadcaster._clients.remove(client_q)
                        except ValueError:
                            pass

            return Response(
                stream_with_context(generate()),
                content_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )


# ---------------------------------------------------------------------------
# Terminal broadcaster — writes raw text to connected xterm WebSocket clients
# ---------------------------------------------------------------------------

_TERM_SKIP = re.compile(
    r"^\s*$"
    r"|GET /log_stream"
    r"|GET /api/status"
    r"|GET /image/"
    r"|GET /static/"
    r"|GET /vendor/"
    r'|HTTP/1\.[01]" [23]\d\d -\s*$'
)


class TerminalBroadcaster:
    """Broadcast raw text/bytes to every connected xterm WebSocket client.

    Keeps a rolling history buffer so a new client (xterm WebSocket) receives
    all output since startup the moment it connects — the user sees everything
    even if they open the Terminal tab minutes after launch.
    """

    def __init__(self, buf_chars: int = 65536) -> None:
        self._sinks: list = []
        self._lock = threading.Lock()
        self._buf_chars = buf_chars
        self._buf: list[str] = []   # ordered chunks; total chars ≤ buf_chars
        self._buf_size = 0

    def _append_buf(self, text: str) -> None:
        """Add text to the ring buffer, dropping the oldest chunk if needed."""
        self._buf.append(text)
        self._buf_size += len(text)
        while self._buf and self._buf_size - len(self._buf[0]) >= self._buf_chars:
            self._buf_size -= len(self._buf.pop(0))

    def add_client(self, send_fn) -> None:
        with self._lock:
            self._sinks.append(send_fn)
            # Replay history so the client catches up immediately.
            for chunk in self._buf:
                try:
                    send_fn(chunk)
                except Exception:
                    pass

    def remove_client(self, send_fn) -> None:
        with self._lock:
            try:
                self._sinks.remove(send_fn)
            except ValueError:
                pass

    def write(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._append_buf(text)
            dead = []
            for fn in list(self._sinks):
                try:
                    fn(text)
                except Exception:
                    dead.append(fn)
            for d in dead:
                try:
                    self._sinks.remove(d)
                except ValueError:
                    pass

    def write_raw(self, data: bytes) -> None:
        self.write(data.decode("utf-8", errors="replace"))

    def install_tee(self) -> None:
        """Redirect sys.stdout and sys.stderr so all Python output also
        appears in the in-app terminal (Werkzeug request-log spam filtered)."""
        broadcaster = self

        class _Tee:
            def __init__(self, orig):
                self._orig = orig

            def write(self, text: str):
                self._orig.write(text)
                t = text.rstrip()
                if t and not _TERM_SKIP.search(t):
                    broadcaster.write(text)

            def flush(self):
                self._orig.flush()

            def __getattr__(self, name):
                return getattr(self._orig, name)

        sys.stdout = _Tee(sys.stdout)
        sys.stderr = _Tee(sys.stderr)


terminal_broadcaster = TerminalBroadcaster()
