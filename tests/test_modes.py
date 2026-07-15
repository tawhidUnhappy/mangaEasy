from __future__ import annotations

import json
import subprocess
import sys

import pytest

from mangaeasy.cli import COMMANDS
from mangaeasy.command_spec import TOOLS
from mangaeasy.mcp_server import _run_tool, _tools_list
from mangaeasy.modes import COMMON_TOOLS, MODES, resolve_skill_path
from mangaeasy.tools.install import TOOLS as EXTERNAL_TOOLS


def test_mode_registries_reference_real_surfaces():
    for key, mode in MODES.items():
        assert mode.commands <= set(COMMANDS)
        assert mode.tools <= set(TOOLS)
        assert set(mode.required_external_tools + mode.optional_external_tools) <= set(EXTERNAL_TOOLS)
        assert (resolve_skill_path(key) / "SKILL.md").is_file()


def test_router_and_scoped_mcp_catalogs_are_isolated():
    assert {tool["name"] for tool in _tools_list()} == COMMON_TOOLS
    story = {tool["name"] for tool in _tools_list("ai-story")}
    song = {tool["name"] for tool in _tools_list("song-video")}
    assert {"story_init", "story_check", "story_build"} <= story
    assert {"song_init", "song_check", "song_build"} <= song
    assert {"youtube_profiles", "youtube_status", "youtube_upload", "youtube_list",
            "youtube_delete", "youtube_thumbnail"} <= story & song
    assert "youtube_upload" not in COMMON_TOOLS
    assert not ({"song_init", "download", "run_full_pipeline"} & story)
    assert not ({"story_init", "download", "run_full_pipeline"} & song)


def test_mode_rejects_hidden_tools_and_job_escape():
    with pytest.raises(ValueError, match="not available"):
        _run_tool("download", {}, "ai-story")
    with pytest.raises(ValueError, match="outside MCP mode"):
        _run_tool("job_start", {"tool": "run_full_pipeline", "arguments": {}}, "ai-story")
    with pytest.raises(ValueError, match="outside MCP mode"):
        _run_tool("install_tool", {"name": "ace-step"}, "ai-story")


def test_mcp_help_does_not_start_server():
    proc = subprocess.run(
        [sys.executable, "-m", "mangaeasy.cli", "mcp", "--help"],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert proc.returncode == 0
    assert "--mode" in proc.stdout
    assert "--allow-root" in proc.stdout


def test_mode_setup_dry_run_is_exact():
    proc = subprocess.run(
        [sys.executable, "-m", "mangaeasy.cli", "setup", "--mode", "song-video", "--dry-run"],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert proc.returncode == 0
    marker = next(line for line in proc.stdout.splitlines() if line.startswith("MANGAEASY_RESULT "))
    payload = json.loads(marker.partition(" ")[2])
    assert payload["tools"] == ["ace-step", "demucs", "whisperx", "z-image-turbo"]
