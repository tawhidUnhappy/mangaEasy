"""The machine-readable CLI contract agents rely on: `commands --json`,
`where --json`, exit codes, and the MANGAEASY_RESULT marker helper."""

import json
import subprocess
import sys

from mangaeasy import __version__
from mangaeasy.cli import COMMANDS, main
from mangaeasy.utils import emit_result


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "mangaeasy.cli", *args],
        capture_output=True, text=True, encoding="utf-8",
    )


def test_commands_json_catalog(capsys):
    assert main(["commands", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["version"] == __version__
    names = {entry["name"] for entry in data["commands"]}
    assert names == set(COMMANDS)
    sample = data["commands"][0]
    assert set(sample) == {"name", "group", "help", "usage"}


def test_where_json_keys(capsys):
    assert main(["where", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    for key in ("version", "platform", "frozen", "app_root", "mangaeasy_home",
                "tools_home", "vendored_bin_dirs", "env_overrides"):
        assert key in data
    assert data["version"] == __version__


def test_tools_json(capsys):
    assert main(["tools", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "tools_home" in data
    assert set(data["tools"]) == {"kokoro-82m", "index-tts", "magi-v3", "deepseek-ocr2", "z-image-turbo"}


def test_emit_result_line_is_parseable(capsys):
    emit_result(outputs=["a/b.mp4"], extra=1)
    line = capsys.readouterr().out.strip()
    assert line.startswith("MANGAEASY_RESULT ")
    payload = json.loads(line[len("MANGAEASY_RESULT "):])
    assert payload == {"outputs": ["a/b.mp4"], "extra": 1}


def test_exit_code_2_on_usage_error():
    proc = run_cli("video-check", "--no-such-flag")
    assert proc.returncode == 2


def test_exit_code_1_on_runtime_failure(tmp_path):
    proc = run_cli("video-check", "--project-root", str(tmp_path / "does-not-exist"))
    assert proc.returncode == 1


def test_piped_stdout_is_utf8():
    """Help output contains non-cp1252 characters (e.g. U+2212); piping it
    must not crash on Windows (the historical failure mode)."""
    proc = run_cli("--help")
    assert proc.returncode == 0
    assert "video" in proc.stdout
