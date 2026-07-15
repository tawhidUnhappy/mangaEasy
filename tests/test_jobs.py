"""Background jobs: start -> supervisor -> status lifecycle, and the guards."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from mangaeasy.jobs import _effective_status, _save_state


def run_cli(*args: str, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "mangaeasy.cli", *args],
        capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=cwd,
    )


def test_job_lifecycle(tmp_path):
    jobs_dir = tmp_path / "jobs"
    start = run_cli("job-start", "--jobs-dir", str(jobs_dir), "where", "--json")
    assert start.returncode == 0, start.stderr
    payload = json.loads(start.stdout)
    assert payload["ok"] is True
    job_id = payload["job_id"]
    assert "where" in job_id

    # The supervisor is detached; poll until it records a final state.
    report = None
    for _ in range(60):
        status = run_cli("job-status", job_id, "--jobs-dir", str(jobs_dir), "--json")
        report = json.loads(status.stdout)
        if report["status"] in ("succeeded", "failed", "orphaned"):
            break
        time.sleep(0.5)
    assert report is not None
    assert report["status"] == "succeeded", report
    assert report["exit_code"] == 0
    assert report["log_tail"], "log tail should contain the child's output"

    listing = run_cli("jobs", "--jobs-dir", str(jobs_dir), "--json")
    jobs = json.loads(listing.stdout)["jobs"]
    assert [j["id"] for j in jobs] == [job_id]
    assert jobs[0]["status"] == "succeeded"


def test_job_start_rejects_unknown_and_denylisted(tmp_path):
    jobs_dir = str(tmp_path / "jobs")
    bad = run_cli("job-start", "--jobs-dir", jobs_dir, "not-a-command")
    assert bad.returncode == 2
    assert json.loads(bad.stdout)["ok"] is False

    recursive = run_cli("job-start", "--jobs-dir", jobs_dir, "mcp")
    assert recursive.returncode == 2
    assert json.loads(recursive.stdout)["ok"] is False


def test_job_start_typed_wrapper_validates_and_builds_cli_args(tmp_path):
    jobs_dir = tmp_path / "jobs"
    arguments = json.dumps({"mode": "manga-video", "dry_run": True})
    start = run_cli(
        "job-start", "--jobs-dir", str(jobs_dir),
        "--tool", "setup", "--arguments-json", arguments,
    )
    assert start.returncode == 0, start.stderr
    payload = json.loads(start.stdout)
    assert payload["tool"] == "setup"
    state = json.loads(Path(payload["state_file"]).read_text(encoding="utf-8"))
    assert state["tool"] == "setup"
    assert state["command"] == "setup"
    assert state["args"] == ["--mode", "manga-video", "--dry-run"]


def test_job_start_typed_wrapper_rejects_bad_arguments(tmp_path):
    jobs_dir = str(tmp_path / "jobs")
    bad = run_cli(
        "job-start", "--jobs-dir", jobs_dir,
        "--tool", "panel_transcript", "--arguments-json", '{"unknown":true}',
    )
    assert bad.returncode == 2
    payload = json.loads(bad.stdout)
    assert payload["ok"] is False
    assert "unknown argument" in payload["error"]


def test_job_status_unknown_id(tmp_path):
    missing = run_cli(
        "job-status", "20260715-120000-video-deadbeef",
        "--jobs-dir", str(tmp_path), "--json",
    )
    assert missing.returncode == 1
    assert json.loads(missing.stdout)["ok"] is False


def test_job_status_rejects_traversal_and_absolute_state_paths(tmp_path):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"status":"succeeded","secret":"do-not-read"}', encoding="utf-8")

    for candidate in ("../outside", str(outside.resolve())):
        result = run_cli(
            "job-status", candidate, "--jobs-dir", str(jobs_dir), "--json",
        )
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert "invalid job id" in payload["error"]
        assert "do-not-read" not in result.stdout


def test_job_status_uses_id_but_rejects_even_contained_state_path(tmp_path):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    job_id = "20260715-120000-video-deadbeef"
    state_file = jobs_dir / f"{job_id}.json"
    outside_log = tmp_path / "must-not-be-read.txt"
    outside_log.write_text('MANGAEASY_RESULT {"secret":"do-not-read"}\n', encoding="utf-8")
    state_file.write_text(json.dumps({
        "id": job_id,
        "command": "video",
        "args": [],
        "status": "succeeded",
        "exit_code": 0,
        "log": str(outside_log),
    }), encoding="utf-8")

    by_id = run_cli("job-status", job_id, "--jobs-dir", str(jobs_dir), "--json")
    assert by_id.returncode == 0
    assert json.loads(by_id.stdout)["id"] == job_id
    assert "do-not-read" not in by_id.stdout

    by_path = run_cli("job-status", str(state_file), "--jobs-dir", str(jobs_dir), "--json")
    assert by_path.returncode == 1
    assert "invalid job id" in json.loads(by_path.stdout)["error"]


def test_dead_supervisor_reports_orphaned():
    # A 'running' record whose supervisor pid no longer exists must not be
    # reported as running forever (machine sleep / kill -9).
    state = {"status": "running", "supervisor_pid": 999_999_999}
    assert _effective_status(state) == "orphaned"
    assert _effective_status({"status": "succeeded", "supervisor_pid": None}) == "succeeded"


def test_state_save_retries_transient_windows_replace_race(tmp_path, monkeypatch):
    state_file = tmp_path / "job.json"
    real_replace = os.replace
    attempts = 0

    def flaky_replace(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("destination is briefly open by job-status")
        return real_replace(source, destination)

    monkeypatch.setattr("mangaeasy.jobs.os.replace", flaky_replace)
    monkeypatch.setattr("mangaeasy.jobs.time.sleep", lambda _seconds: None)

    _save_state(state_file, {"status": "succeeded", "exit_code": 0})

    assert json.loads(state_file.read_text(encoding="utf-8"))["status"] == "succeeded"
    assert attempts == 3
    assert list(tmp_path.glob("*.tmp")) == []
