"""mangaeasy.web.app.api_terminal — xterm.js WebSocket PTY terminal.

Spawns a real shell (cmd.exe on Windows, $SHELL on Unix) connected to a PTY
so the client xterm.js instance gets proper ANSI sequences and interactive
behaviour.  Registered via register_ws(sock) from create_app().
"""

from __future__ import annotations

import json
import os
import sys
import threading

from mangaeasy.web.app.state import state


def _cwd() -> str:
    root = state.get("project_root")
    return str(root) if root and str(root) != "None" else "."


def register_ws(sock) -> None:
    """Attach the /ws/terminal WebSocket route to *sock* (a flask_sock.Sock)."""

    @sock.route("/ws/terminal")
    def ws_terminal(ws):
        cwd = _cwd()
        env = {**os.environ, "MANGAEASY_PROJECT_ROOT": cwd, "PYTHONIOENCODING": "utf-8"}

        if sys.platform == "win32":
            _run_win32(ws, cwd, env)
        else:
            _run_unix(ws, cwd, env)


# ── Windows — winpty PtyProcess ─────────────────────────────────────────────

def _run_win32(ws, cwd: str, env: dict) -> None:
    try:
        from winpty import PtyProcess
    except ImportError:
        ws.send("\r\n\x1b[31mpywinpty not installed — cannot open terminal\x1b[0m\r\n")
        return

    shell = os.environ.get("COMSPEC", "cmd.exe")
    proc = PtyProcess.spawn([shell], cwd=cwd, env=env, dimensions=(24, 220))

    stop = threading.Event()

    def _read():
        while not stop.is_set() and proc.isalive():
            try:
                data = proc.read(4096)
                if data:
                    ws.send(data)
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

def _run_unix(ws, cwd: str, env: dict) -> None:
    import pty
    import select
    import fcntl
    import termios
    import struct
    import signal

    shell = env.get("SHELL", "/bin/bash")
    pid, fd = pty.fork()

    if pid == 0:
        # Child: exec the shell
        try:
            os.chdir(cwd)
        except OSError:
            pass
        os.execvpe(shell, [shell], env)
        os._exit(1)

    # Parent
    stop = threading.Event()

    def _read():
        while not stop.is_set():
            try:
                r, _, _ = select.select([fd], [], [], 0.1)
                if r:
                    data = os.read(fd, 4096)
                    if data:
                        ws.send(data.decode("utf-8", errors="replace"))
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
