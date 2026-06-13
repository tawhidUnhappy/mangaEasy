"""mangaeasy.web.app.server — startup: desktop window (pywebview) or browser."""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from pathlib import Path

from flask import Flask

from mangaeasy.web.app.jobs import cleanup
from mangaeasy.web.app.state import state

DEFAULT_PORT = 5010


# ── Windows taskbar / window-icon helpers ─────────────────────────────────────

def _win_setup() -> None:
    """Set AppUserModelID so Windows groups our windows under mangaEasy, not Python."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("com.mangaeasy.app")
    except Exception:
        pass


def _win_apply_icon() -> None:
    """Load the bundled icon.ico and push it onto the pywebview window via Win32."""
    try:
        import ctypes
        # Give pywebview a moment to finish creating the Win32 window.
        for _ in range(20):
            win = state.get("window")
            hwnd = getattr(win, "native_handle", None)
            if hwnd:
                break
            time.sleep(0.1)
        else:
            return

        user32 = ctypes.windll.user32
        WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
        LR_LOADFROMFILE, IMAGE_ICON = 0x10, 1

        # Prefer the bundled icon.ico (has all sizes incl. 256×256 for HiDPI).
        if getattr(sys, "frozen", False):
            ico = Path(sys.executable).parent / "icon.ico"
        else:
            ico = Path(__file__).parent.parent.parent.parent / "packaging" / "icon.ico"

        if ico.exists():
            hBig = user32.LoadImageW(None, str(ico), IMAGE_ICON, 256, 256, LR_LOADFROMFILE)
            hSmall = user32.LoadImageW(None, str(ico), IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        else:
            # Fall back to extracting from the EXE itself.
            shell32 = ctypes.windll.shell32
            HICONp = ctypes.c_void_p
            hBig, hSmall = HICONp(0), HICONp(0)
            shell32.ExtractIconExW(sys.executable, 0, ctypes.byref(hBig), ctypes.byref(hSmall), 1)

        if hBig:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hBig)
        if hSmall:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hSmall)
    except Exception:
        pass


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

    if sys.platform == "win32":
        _win_setup()

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

    from mangaeasy.web.app import window_state as ws
    saved = ws.load()

    # Keep the window handle so the pick-folder/file APIs can show native dialogs.
    win = window.create_window(
        "mangaEasy", url,
        width=saved["width"], height=saved["height"],
        x=saved["x"], y=saved["y"],
        min_size=(900, 600),
    )
    state["window"] = win

    # Save geometry when the user closes the window.
    win.events.closing += lambda: ws.save(win)

    def _start_func():
        if sys.platform == "win32":
            _win_apply_icon()
        if saved["maximized"]:
            win.maximize()

    window.start(func=_start_func)
    state["window"] = None
    cleanup()
    return 0
