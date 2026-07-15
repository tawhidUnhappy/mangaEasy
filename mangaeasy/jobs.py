"""mangaeasy.jobs — detached background jobs with a queryable state file.

Almost every real MediaConductor step runs for minutes to hours (TTS, panel
detection, OCR, renders, uploads). Blocking a caller — an MCP tools/call, a
script, an agent's foreground shell — for that long is the wrong shape, and
"spawn it yourself and forensically infer liveness from log mtimes and
nvidia-smi" was the documented workaround. This module replaces that:

    mediaconductor job-start video --project-root library/X --items 01-12
    -> {"ok": true, "job_id": "20260714-153000-video-a1b2c3d4", ...}   (returns instantly)
    mediaconductor job-status 20260714-153000-video-a1b2c3d4 --json
    -> status running/succeeded/failed/orphaned, exit code, last
       MANGAEASY_PROGRESS marker, parsed MANGAEASY_RESULT, log tail
    mediaconductor jobs --json
    -> every job, newest first

How it works: `job-start` writes `<jobs-dir>/<id>.json` and spawns a detached
supervisor (`job-run`, internal) which runs the real command with its output
redirected to `<id>.log`, then records the exit code and the final
MANGAEASY_RESULT payload into the state file. Because the *supervisor* owns
the final write, `job-status` can report a truthful exit code after the fact;
if the supervisor pid is gone without a final write (machine slept, kill -9),
the job is reported `orphaned` rather than forever "running".

Jobs dir: `<work-dir>/jobs` (MANGAEASY_JOBS_DIR overrides). State files are
small JSON; logs are plain text. Both are safe to delete when a job is done.
"""

from __future__ import annotations

import argparse
from collections import deque
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from mangaeasy.brand import CLI_NAME, PRODUCT_NAME
from mangaeasy.runtime import cli_command, popen_kwargs
from mangaeasy.video_pipeline.common import DEFAULT_WORK_DIR

# Commands a job must not wrap: the server (never terminates), and the job
# commands themselves (recursion).
_DENYLIST = {"mcp", "job-start", "job-run", "job-status", "jobs"}

_TAIL_DEFAULT = 20
_STILL_ACTIVE = 259  # Windows GetExitCodeProcess sentinel
_JOB_ID_RE = re.compile(
    r"\A\d{8}-\d{6}-[a-z0-9]+(?:-[a-z0-9]+)*-[0-9a-f]{8}\Z"
)


def jobs_dir() -> Path:
    configured = os.environ.get("MANGAEASY_JOBS_DIR")
    if configured:
        return Path(configured)
    return DEFAULT_WORK_DIR / "jobs"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _save_state(path: Path, state: dict) -> None:
    """Atomically persist a job state, tolerating short Windows reader races.

    A fixed ``.tmp`` filename lets two closely spaced supervisor writes collide.
    On Windows, replacing the destination can also fail briefly while another
    process is opening it for ``job-status``.  Use a per-write temporary file
    and retry only the atomic replace; never expose a partially written JSON
    document to readers.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        for attempt in range(50):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.02)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _load_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _state_file_for_id(job_id: str, base: Path) -> Path:
    """Resolve a generated job id below *base*; reject paths and traversal."""
    if _JOB_ID_RE.fullmatch(job_id) is None:
        raise ValueError("invalid job id; pass the id returned by job-start, not a file path")
    resolved_base = base.expanduser().resolve()
    state_file = (resolved_base / f"{job_id}.json").resolve()
    if not state_file.is_relative_to(resolved_base):
        raise ValueError("job state file resolves outside --jobs-dir")
    return state_file


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if sys.platform == "win32":
        import ctypes
        import ctypes.wintypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _detached_popen_kwargs() -> dict:
    """Survive the parent's death: new session (POSIX) / detached process (Windows)."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _scan_log_markers(log_path: Path) -> tuple[str | None, dict | None]:
    """(last MANGAEASY_PROGRESS line, parsed MANGAEASY_RESULT payload) from the log."""
    progress = None
    result = None
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("MANGAEASY_PROGRESS "):
                    progress = line[len("MANGAEASY_PROGRESS "):].strip()
                elif line.startswith("MANGAEASY_RESULT "):
                    try:
                        result = json.loads(line[len("MANGAEASY_RESULT "):])
                    except ValueError:
                        pass
    except OSError:
        return None, None
    return progress, result


def _effective_status(state: dict) -> str:
    """The trustworthy status: a 'running' job whose supervisor died is orphaned."""
    status = state.get("status", "unknown")
    if status in ("starting", "running") and not _pid_alive(state.get("supervisor_pid")):
        return "orphaned"
    return status


# ── job-start ────────────────────────────────────────────────────────────────

def start_main() -> int:
    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} job-start",
        description=f"Run a long-running {PRODUCT_NAME} tool as a detached background job. "
                    "Prefer the typed --tool/--arguments-json form; the legacy positional "
                    "command form remains accepted. Prints one JSON object with the job id.")
    parser.add_argument("--tool",
                        help="Typed MCP tool name, e.g. 'run_full_pipeline'.")
    parser.add_argument("--arguments-json", default="{}", metavar="OBJECT",
                        help="JSON object matching --tool's machine schema (default: {}).")
    parser.add_argument("command", nargs="?",
                        help="Compatibility form: CLI command name, e.g. 'video'.")
    parser.add_argument("args", nargs=argparse.REMAINDER,
                        help="Compatibility form: arguments passed through verbatim.")
    parser.add_argument("--jobs-dir", type=Path, default=None,
                        help="Where job state/log files live (default: <work>/jobs).")
    args = parser.parse_args()

    from mangaeasy.cli import COMMANDS  # late import: cli imports nothing heavy

    typed_tool = args.tool
    if typed_tool and (args.command is not None or args.args):
        parser.error("use either --tool/--arguments-json or the positional command form, not both")

    if typed_tool:
        from mangaeasy.command_spec import LONG_RUNNING, TOOLS
        from mangaeasy.mcp_server import _build_args

        if typed_tool not in TOOLS or typed_tool == "job_start":
            print(json.dumps({"ok": False, "error": f"unknown or recursive tool: {typed_tool}"}))
            return 2
        command = TOOLS[typed_tool][0]
        if command not in LONG_RUNNING:
            print(json.dumps({
                "ok": False,
                "error": f"tool '{typed_tool}' is not marked long-running; call it directly",
            }))
            return 2
        try:
            arguments = json.loads(args.arguments_json)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": f"--arguments-json is invalid JSON: {exc}"}))
            return 2
        if not isinstance(arguments, dict):
            print(json.dumps({"ok": False, "error": "--arguments-json must be a JSON object"}))
            return 2
        try:
            command_args = _build_args(typed_tool, arguments)
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}))
            return 2
    else:
        if args.command is None:
            parser.error("provide --tool or a positional command")
        command = args.command
        command_args = list(args.args)

    if command not in COMMANDS:
        print(json.dumps({"ok": False, "error": f"unknown command: {command}"}))
        return 2
    if command in _DENYLIST:
        print(json.dumps({"ok": False, "error": f"'{command}' cannot run as a job"}))
        return 2

    base = args.jobs_dir or jobs_dir()
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    job_id = f"{stamp}-{command}-{uuid4().hex[:8]}"
    state_file = base / f"{job_id}.json"
    log_file = base / f"{job_id}.log"

    state = {
        "id": job_id,
        "command": command,
        "args": command_args,
        "status": "starting",
        "started_at": _now_iso(),
        "log": str(log_file.resolve()),
        "state_file": str(state_file.resolve()),
        "supervisor_pid": None,
        "child_pid": None,
        "exit_code": None,
    }
    if typed_tool:
        state["tool"] = typed_tool
    _save_state(state_file, state)

    supervisor = subprocess.Popen(
        cli_command("job-run", str(state_file.resolve())),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **_detached_popen_kwargs(),
    )
    # Wait briefly for the supervisor to claim the state file, so the caller's
    # very next job-status doesn't race a still-empty record.
    for _ in range(50):
        current = _load_state(state_file)
        if current.get("supervisor_pid"):
            state = current
            break
        if supervisor.poll() is not None:
            break
        time.sleep(0.1)

    print(json.dumps({
        "ok": True, "job_id": job_id, "command": command, "tool": typed_tool,
        "state_file": str(state_file.resolve()), "log": str(log_file.resolve()),
        "poll": f"{CLI_NAME} job-status {job_id} --json",
    }, ensure_ascii=False))
    return 0


# ── job-run (internal supervisor) ────────────────────────────────────────────

def run_main() -> int:
    parser = argparse.ArgumentParser(description="(internal) job-start's supervisor.")
    parser.add_argument("state_file", type=Path)
    args = parser.parse_args()

    state = _load_state(args.state_file)
    state["supervisor_pid"] = os.getpid()
    state["status"] = "running"
    _save_state(args.state_file, state)

    log_path = Path(state["log"])
    with open(log_path, "a", encoding="utf-8", errors="replace") as log:
        child = subprocess.Popen(
            cli_command(state["command"], *state["args"]),
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            **popen_kwargs(),
        )
        state["child_pid"] = child.pid
        _save_state(args.state_file, state)
        rc = child.wait()

    _progress, result = _scan_log_markers(log_path)
    state["status"] = "succeeded" if rc == 0 else "failed"
    state["exit_code"] = rc
    state["finished_at"] = _now_iso()
    if result is not None:
        state["result"] = result
    _save_state(args.state_file, state)
    return 0


# ── job-status ───────────────────────────────────────────────────────────────

def _status_report(state_file: Path, tail: int) -> dict:
    state = _load_state(state_file)
    status = _effective_status(state)
    # The state record is local data, but never trust its `log` value as a
    # read path. A crafted JSON file must not turn job-status into an arbitrary
    # file reader; generated logs are always siblings of their state file.
    log_path = state_file.with_suffix(".log")
    progress, result = _scan_log_markers(log_path)
    report = {
        "ok": status == "succeeded" or status == "running" or status == "starting",
        "id": state.get("id"),
        "command": state.get("command"),
        "args": state.get("args"),
        "status": status,
        "exit_code": state.get("exit_code"),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "progress": progress,
        "result": state.get("result", result),
        "log": str(log_path.resolve()),
    }
    if tail > 0 and log_path.exists():
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            report["log_tail"] = list(deque((line.rstrip("\r\n") for line in handle), maxlen=min(tail, 500)))
    return report


def status_main() -> int:
    parser = argparse.ArgumentParser(
        description="Status of one background job started by job-start.")
    parser.add_argument("job_id", help="Generated id returned by job-start (file paths are rejected).")
    parser.add_argument("--tail", type=int, default=_TAIL_DEFAULT,
                        help=f"Log tail lines to include (default {_TAIL_DEFAULT}).")
    parser.add_argument("--jobs-dir", type=Path, default=None)
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit one JSON object on stdout.")
    args = parser.parse_args()
    if args.tail < 0 or args.tail > 500:
        parser.error("--tail must be between 0 and 500")

    try:
        state_file = _state_file_for_id(args.job_id, args.jobs_dir or jobs_dir())
    except ValueError as exc:
        message = {"ok": False, "error": str(exc)}
        print(json.dumps(message) if args.as_json else f"[job-status] {message['error']}")
        return 1
    if not state_file.exists():
        message = {"ok": False, "error": f"no such job: {args.job_id}"}
        print(json.dumps(message) if args.as_json else f"[job-status] {message['error']}")
        return 1

    report = _status_report(state_file, args.tail)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"{report['id']}: {report['status']}"
              + (f" (exit {report['exit_code']})" if report["exit_code"] is not None else ""))
        if report.get("progress"):
            print(f"  progress: {report['progress']}")
        for line in report.get("log_tail", []):
            print(f"  | {line}")
    return 0


# ── jobs (list) ──────────────────────────────────────────────────────────────

def list_main() -> int:
    parser = argparse.ArgumentParser(description="List background jobs, newest first.")
    parser.add_argument("--jobs-dir", type=Path, default=None)
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit one JSON object on stdout.")
    args = parser.parse_args()

    base = args.jobs_dir or jobs_dir()
    entries = []
    if base.is_dir():
        for state_file in sorted(base.glob("*.json"), reverse=True):
            try:
                state = _load_state(state_file)
            except (OSError, ValueError):
                continue
            entries.append({
                "id": state.get("id", state_file.stem),
                "command": state.get("command"),
                "status": _effective_status(state),
                "exit_code": state.get("exit_code"),
                "started_at": state.get("started_at"),
                "finished_at": state.get("finished_at"),
            })
    if args.as_json:
        print(json.dumps({"ok": True, "jobs_dir": str(base.resolve()), "jobs": entries},
                         ensure_ascii=False))
    else:
        if not entries:
            print(f"[jobs] none under {base}")
        for entry in entries:
            print(f"{entry['id']:<44} {entry['status']:<10} {entry['command']}")
    return 0
