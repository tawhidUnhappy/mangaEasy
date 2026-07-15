"""The MCP stdio server: handshake, tool catalog, and a real tool call."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import mangaeasy.mcp_server as mcp_server
from mangaeasy.mcp_server import (
    MAX_BRIDGED_TEXT_BYTES,
    _bridge_inline_text,
    _build_args,
    _enforce_workspace_policy,
    _resolve_allowed_roots,
    _run_tool,
)


def mcp_session(*messages: dict) -> list[dict]:
    stdin = "".join(json.dumps(m) + "\n" for m in messages)
    proc = subprocess.run(
        [sys.executable, "-m", "mangaeasy.cli", "mcp"],
        input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def test_initialize_and_tools_list():
    replies = mcp_session(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )
    by_id = {r["id"]: r for r in replies}
    assert by_id[1]["result"]["serverInfo"]["name"] == "media-conductor"
    tools = by_id[2]["result"]["tools"]
    assert {t["name"] for t in tools} == {
        "modes", "setup", "doctor", "where", "install_tool",
        "youtube_profiles", "youtube_status", "job_start", "job_status", "job_list",
    }
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"


def test_where_tool_call():
    replies = mcp_session(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "where", "arguments": {}}},
    )
    reply = next(r for r in replies if r.get("id") == 2)
    body = json.loads(reply["result"]["content"][0]["text"])
    assert body["exit_code"] == 0
    assert "app_root" in body["report"]


def test_unknown_tool_is_an_error():
    replies = mcp_session(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "nope", "arguments": {}}},
    )
    reply = next(r for r in replies if r.get("id") == 2)
    assert reply["error"]["code"] == -32602


def test_build_args_shapes():
    assert _build_args("library_list", {"project_root": "/p"}) == \
        ["--project-root", "/p", "--json"]
    args = _build_args("video_check", {"project_root": "/p", "items": ["01", "05-08"]})
    assert args == ["--project-root", "/p", "--items", "01", "05-08", "--json"]
    # no-flag kind: require_long=False adds --no-require-long; True adds nothing
    assert "--no-require-long" in _build_args("video_validate", {"project_root": "/p", "require_long": False})
    assert "--no-require-long" not in _build_args("video_validate", {"project_root": "/p", "require_long": True})
    assert _build_args("install_tool", {"name": "kokoro-82m", "update": True}) == ["kokoro-82m", "--update"]


def test_build_args_missing_required():
    with pytest.raises(ValueError):
        _build_args("library_list", {})


@pytest.mark.parametrize("bad_params", [[], "not-an-object", 7, None])
def test_non_object_params_return_invalid_params(bad_params):
    replies = mcp_session({
        "jsonrpc": "2.0", "id": 41, "method": "tools/list", "params": bad_params,
    })
    assert replies == [{
        "jsonrpc": "2.0",
        "id": 41,
        "error": {"code": -32602, "message": "params must be an object"},
    }]


def test_non_object_tool_arguments_return_invalid_params():
    replies = mcp_session({
        "jsonrpc": "2.0", "id": 42, "method": "tools/call",
        "params": {"name": "where", "arguments": ["unexpected"]},
    })
    assert replies[0]["error"] == {"code": -32602, "message": "arguments must be an object"}


def test_large_inline_story_uses_private_temp_file_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path))
    story = "private opening\n" + ("scene continuity " * 20_000)
    original = {"project_root": "D:/stories/demo", "title": "Demo", "story": story}

    with _bridge_inline_text("story_init", original) as (bridged, paths):
        assert "story" not in bridged
        assert len(paths) == 1
        path = paths[0]
        assert bridged["story_file"] == str(path)
        assert path.read_text(encoding="utf-8") == story
        argv = _build_args("story_init", bridged)
        assert "--story-file" in argv
        assert "--story" not in argv
        assert story not in argv
        if os.name != "nt":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600

    assert not path.exists()


def test_inline_text_bridge_is_bounded_and_creates_no_file_on_rejection(tmp_path, monkeypatch):
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path))
    too_large = "x" * (MAX_BRIDGED_TEXT_BYTES + 1)
    with pytest.raises(ValueError, match="MCP text limit"):
        with _bridge_inline_text("song_init", {
            "project_root": "D:/songs/demo", "title": "Demo", "lyrics": too_large,
        }):
            pass
    temp_root = tmp_path / "tmp" / "mcp"
    assert not temp_root.exists() or not list(temp_root.iterdir())


def test_run_tool_bridges_lyrics_redacts_log_and_internal_path(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path))
    lyrics = "LYRICS_MUST_NOT_LEAK\n" + ("long chorus " * 10_000)
    captured: dict = {}

    def fake_run(argv, **_kwargs):
        captured["argv"] = list(argv)
        file_flag = argv.index("--lyrics-file")
        path = Path(argv[file_flag + 1])
        captured["path"] = path
        assert path.is_file()
        assert path.read_text(encoding="utf-8") == lyrics
        return subprocess.CompletedProcess(
            argv, 0, stdout='{"ok": true}\n', stderr=f"input was {path}",
        )

    monkeypatch.setattr(mcp_server.subprocess, "run", fake_run)
    body, is_error = _run_tool("song_init", {
        "project_root": "D:/PRIVATE_PROJECT", "title": "PRIVATE_TITLE", "lyrics": lyrics,
    }, mode="song-video")

    assert is_error is False
    assert not captured["path"].exists()
    assert lyrics not in captured["argv"]
    assert "--lyrics" not in captured["argv"]
    assert "--lyrics-file" in captured["argv"]
    report = json.loads(body)
    assert report["stderr"] == "input was <mcp-temp-file>"
    server_log = capsys.readouterr().err
    assert "LYRICS_MUST_NOT_LEAK" not in server_log
    assert "D:/PRIVATE_PROJECT" not in server_log
    assert "PRIVATE_TITLE" not in server_log
    assert str(captured["path"]) not in server_log
    assert "tool=song_init" in server_log
    assert "argument_names=lyrics,project_root,title" in server_log


def test_bridge_temp_file_is_cleaned_when_child_launch_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path))
    captured = {}

    def failed_run(argv, **_kwargs):
        path = Path(argv[argv.index("--story-file") + 1])
        captured["path"] = path
        assert path.exists()
        raise OSError("synthetic launch failure")

    monkeypatch.setattr(mcp_server.subprocess, "run", failed_run)
    with pytest.raises(OSError, match="synthetic launch failure"):
        _run_tool("story_init", {
            "project_root": "D:/stories/demo", "title": "Demo", "story": "secret story",
        }, mode="ai-story")
    assert not captured["path"].exists()


def test_mcp_run_log_redacts_description_and_all_argv_values(monkeypatch, capsys):
    def fake_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout='{"ok": true}\n', stderr="")

    monkeypatch.setattr(mcp_server.subprocess, "run", fake_run)
    _run_tool("youtube_upload", {
        "video": "D:/SECRET_VIDEO.mp4",
        "title": "SECRET_TITLE",
        "description": "SECRET_DESCRIPTION",
    }, all_tools=True)
    server_log = capsys.readouterr().err
    assert "SECRET_VIDEO" not in server_log
    assert "SECRET_TITLE" not in server_log
    assert "SECRET_DESCRIPTION" not in server_log
    assert "argument_names=description,title,video" in server_log


def test_workspace_policy_defaults_to_cwd_and_requires_existing_roots(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _resolve_allowed_roots() == (tmp_path.resolve(),)
    with pytest.raises(ValueError, match="existing directory"):
        _resolve_allowed_roots([tmp_path / "missing"])


def test_workspace_policy_accepts_inside_output_and_rejects_outside(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    _enforce_workspace_policy(
        "generate_image",
        {"prompt": "sky", "output": str(allowed / "art.png")},
        (allowed.resolve(),),
    )
    with pytest.raises(ValueError, match="outside the MCP --allow-root"):
        _enforce_workspace_policy(
            "generate_image",
            {"prompt": "sky", "output": str(outside / "art.png")},
            (allowed.resolve(),),
        )


@pytest.mark.parametrize(
    ("tool", "arguments", "bad_name"),
    [
        (
            "style_detect",
            {"project_root": ".", "source_subdir": "../secrets"},
            "source_subdir",
        ),
        (
            "download",
            {"url": "00000000-0000-0000-0000-000000000000", "name": "../escape"},
            "name",
        ),
        (
            "video_check",
            {"project_root": ".", "project_name": "..\\escape"},
            "project_name",
        ),
    ],
)
def test_workspace_policy_rejects_relative_traversal(tool, arguments, bad_name, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match=bad_name):
        _enforce_workspace_policy(tool, arguments, (tmp_path.resolve(),))


def test_workspace_policy_applies_to_nested_background_job(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    with pytest.raises(ValueError, match="outside the MCP --allow-root"):
        _run_tool(
            "job_start",
            {
                "tool": "generate_image",
                "arguments": {"prompt": "sky", "output": str(outside / "art.png")},
            },
            mode="manga-video",
            allowed_roots=(allowed.resolve(),),
        )


def test_workspace_policy_checks_song_manifest_embedded_paths(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    manifest = allowed / "song.json"
    manifest.write_text(json.dumps({
        "audio": {"source": str(outside / "song.wav")},
        "render": {"lyrics_style": {"font_file": "@bundled/edosz.ttf"}},
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="audio.source"):
        _enforce_workspace_policy(
            "song_build",
            {"manifest": str(manifest), "stage": "all"},
            (allowed.resolve(),),
        )


def test_workspace_policy_checks_story_publish_voice_state(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    review = allowed / "review"
    review.mkdir(parents=True)
    outside.mkdir()
    (allowed / "story.json").write_text("{}", encoding="utf-8")
    (review / "video_generation.json").write_text(json.dumps({
        "speaker_wav": str(outside / "voice.wav"),
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="speaker_wav"):
        _enforce_workspace_policy(
            "story_build",
            {"project_root": str(allowed), "stage": "publish"},
            (allowed.resolve(),),
        )


def test_every_mcp_path_property_is_registered_for_workspace_validation():
    path_names = {
        "project_root", "work_dir", "audio_root", "output_root", "overrides",
        "file", "base", "output", "spec_json", "background_music", "speaker_wav",
        "video", "thumbnail", "image", "story_file", "lyrics_file", "audio",
        "output_dir", "manifest", "source_subdir", "old_run",
    }
    for tool, (_cli, _description, properties, _required, _flags) in mcp_server.TOOLS.items():
        classified = (
            mcp_server._PATH_ARGUMENTS.get(tool, frozenset())
            | mcp_server._RELATIVE_PATH_ARGUMENTS.get(tool, frozenset())
            | mcp_server._PORTABLE_SEGMENT_ARGUMENTS.get(tool, frozenset())
        )
        assert (set(properties) & path_names) <= classified, tool


def test_public_mcp_server_rejects_outside_path_before_tool_launch(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    request = {
        "jsonrpc": "2.0",
        "id": 77,
        "method": "tools/call",
        "params": {
            "name": "generate_image",
            "arguments": {"prompt": "sky", "output": str(outside / "art.png")},
        },
    }
    proc = subprocess.run(
        [
            sys.executable, "-m", "mangaeasy.cli", "mcp",
            "--mode", "manga-video", "--allow-root", str(allowed),
        ],
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    reply = json.loads(proc.stdout.strip())
    assert reply["id"] == 77
    assert reply["error"]["code"] == -32602
    assert "outside the MCP --allow-root" in reply["error"]["message"]
