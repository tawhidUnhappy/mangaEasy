"""mangaeasy.web.app.jobs — subprocess job and editor process management."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

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
    full = [sys.executable, "-m", "mangaeasy.cli", command, *args]
    log(f"$ mangaeasy {command} {' '.join(args)}")
    return subprocess.Popen(
        full,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # No terminal behind GUI runs — a child that reads stdin must fail
        # fast (EOF) instead of hanging forever on a prompt nobody can see.
        stdin=subprocess.DEVNULL,
        text=True,
        bufsize=1,
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
