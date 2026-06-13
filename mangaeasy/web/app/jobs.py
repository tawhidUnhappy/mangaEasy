"""mangaeasy.web.app.jobs — subprocess job and editor process management."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from mangaeasy.runtime import cli_command, popen_kwargs
from mangaeasy.web.app.state import log, state


def job_running() -> bool:
    job = state["job"]
    return bool(job and job["thread"].is_alive())


def job_info() -> dict | None:
    job = state["job"]
    if not job:
        return None
    return {"kind": job["kind"], "name": job["name"], "running": job["thread"].is_alive()}


def spawn_cli(command: str, args: list[str], cwd: Path) -> subprocess.Popen:
    env = dict(os.environ)
    env["MANGAEASY_PROJECT_ROOT"] = str(cwd)
    env.setdefault("PYTHONUNBUFFERED", "1")
    # Force UTF-8 I/O so subprocess print() calls with non-ASCII characters
    # (arrows, checkmarks, …) don't crash on Windows where the default is cp1252.
    env["PYTHONIOENCODING"] = "utf-8"
    full = cli_command(command, *args)
    log(f"$ mangaeasy {command} {' '.join(args)}")
    return subprocess.Popen(
        full,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        bufsize=1,
        **popen_kwargs(),
    )


def pump(proc: subprocess.Popen, label: str) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        log(line.rstrip("\n"))
    code = proc.wait()
    log(f"[{label}] finished with exit code {code}")


def cleanup() -> None:
    job = state["job"]
    if job and job.get("proc") and job["proc"].poll() is None:
        job["proc"].terminate()
    for proc in state["editors"].values():
        if proc.poll() is None:
            proc.terminate()
