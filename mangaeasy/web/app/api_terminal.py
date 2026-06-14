"""mangaeasy.web.app.api_terminal — xterm.js WebSocket PTY terminal.

Spawns a git bash shell (Windows) or $SHELL (Unix) connected to a PTY so
the client xterm.js instance gets proper ANSI sequences and interactive
behaviour.  Job output from the subprocess runner is also broadcast to the
same xterm WebSocket via TerminalBroadcaster, so the user sees everything in
one place.

Registered via register_ws(sock) from create_app().
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading

from mangaeasy.web.flask_utils import terminal_broadcaster
from mangaeasy.web.app.state import state


def _cwd() -> str:
    root = state.get("project_root")
    return str(root) if root and str(root) != "None" else "."


def _get_win32_shell() -> list[str]:
    """Prefer git bash; fall back to cmd.exe."""
    # shutil.which respects PATH — git installer adds bash.exe there
    bash = shutil.which("bash")
    if bash:
        return [bash, "--login", "-i"]
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if os.path.exists(candidate):
            return [candidate, "--login", "-i"]
    return [os.environ.get("COMSPEC", "cmd.exe")]


def register_ws(sock) -> None:
    """Attach the /ws/terminal WebSocket route to *sock* (a flask_sock.Sock)."""

    @sock.route("/ws/terminal")
    def ws_terminal(ws):
        # Thread-safe send: both the PTY reader thread and terminal_broadcaster
        # can call safe_send concurrently.
        ws_lock = threading.Lock()

        def safe_send(text: str) -> None:
            try:
                with ws_lock:
                    ws.send(text)
            except Exception:
                pass

        terminal_broadcaster.add_client(safe_send)
        cwd = _cwd()
        env = {
            **os.environ,
            "MANGAEASY_PROJECT_ROOT": cwd,
            "PYTHONIOENCODING": "utf-8",
            "TERM": "xterm-256color",
            "COLORTERM": "truecolor",
        }

        try:
            if sys.platform == "win32":
                _run_win32(ws, cwd, env, safe_send)
            else:
                _run_unix(ws, cwd, env, safe_send)
        finally:
            terminal_broadcaster.remove_client(safe_send)


# ── Windows — winpty PtyProcess ─────────────────────────────────────────────

def _run_win32(ws, cwd: str, env: dict, safe_send) -> None:
    try:
        from winpty import PtyProcess
    except ImportError:
        safe_send("\r\n\x1b[31mpywinpty not installed — cannot open shell\x1b[0m\r\n")
        return

    shell = _get_win32_shell()
    proc = PtyProcess.spawn(shell, cwd=cwd, env=env, dimensions=(24, 220))

    stop = threading.Event()

    def _read():
        while not stop.is_set() and proc.isalive():
            try:
                data = proc.read(4096)
                if data:
                    safe_send(data)
            except Exception:
                break
        try:
            ws.close()
        except Exception:
            pass

    threading.Thread(target=_read, daemon=True).start()

    try:
        while True:
            msg = ws.receive()
            if msg is None:
                break
            try:
                pkt = json.loads(msg)
                if pkt.get("type") == "resize":
                    proc.setwinsize(int(pkt["rows"]), int(pkt["cols"]))
            except (json.JSONDecodeError, KeyError, ValueError):
                proc.write(msg)
    except Exception:
        pass
    finally:
        stop.set()
        if proc.isalive():
            proc.terminate()


# ── Unix — stdlib pty ───────────────────────────────────────────────────────

def _run_unix(ws, cwd: str, env: dict, safe_send) -> None:
    import pty
    import select
    import fcntl
    import termios
    import struct
    import signal

    shell = env.get("SHELL", "/bin/bash")
    pid, fd = pty.fork()

    if pid == 0:
        try:
            os.chdir(cwd)
        except OSError:
            pass
        os.execvpe(shell, [shell, "--login", "-i"], env)
        os._exit(1)

    stop = threading.Event()

    def _read():
        while not stop.is_set():
            try:
                r, _, _ = select.select([fd], [], [], 0.1)
                if r:
                    data = os.read(fd, 4096)
                    if data:
                        safe_send(data.decode("utf-8", errors="replace"))
            except OSError:
                break
        try:
            ws.close()
        except Exception:
            pass

    threading.Thread(target=_read, daemon=True).start()

    try:
        while True:
            msg = ws.receive()
            if msg is None:
                break
            try:
                pkt = json.loads(msg)
                if pkt.get("type") == "resize":
                    rows, cols = int(pkt["rows"]), int(pkt["cols"])
                    winsize = struct.pack("HHHH", rows, cols, 0, 0)
                    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
            except (json.JSONDecodeError, KeyError, ValueError):
                payload = msg.encode() if isinstance(msg, str) else msg
                os.write(fd, payload)
    except Exception:
        pass
    finally:
        stop.set()
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except Exception:
            pass
        try:
            os.close(fd)
        except Exception:
            pass
