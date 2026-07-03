"""The MCP stdio server: handshake, tool catalog, and a real tool call."""

import json
import subprocess
import sys

from mangaeasy.mcp_server import TOOLS, _build_args


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
    assert by_id[1]["result"]["serverInfo"]["name"] == "mangaeasy"
    tools = by_id[2]["result"]["tools"]
    assert {t["name"] for t in tools} == set(TOOLS)
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
    import pytest

    with pytest.raises(ValueError):
        _build_args("library_list", {})
