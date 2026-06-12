"""mangaeasy.web.app.server — startup: desktop window (pywebview) or browser."""

from __future__ import annotations

import argparse
import socket
import threading
import time

from flask import Flask

from mangaeasy.web.app.jobs import cleanup
from mangaeasy.web.app.state import state

DEFAULT_PORT = 5010


def _wait_for_port(port: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def run(app: Flask) -> int:
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
            cleanup()
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

    # Keep the window handle so the pick-folder/file APIs can show native dialogs.
    state["window"] = window.create_window(
        "mangaEasy", url, width=1240, height=820, min_size=(900, 600)
    )
    window.start()
    state["window"] = None
    cleanup()
    return 0
