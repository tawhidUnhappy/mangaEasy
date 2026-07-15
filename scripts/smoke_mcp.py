"""Perform a real MCP initialize/tools-list exchange with a CLI command."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import NoReturn


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"MCP smoke test failed: {message}")


def main(argv: list[str] | None = None) -> int:
    command = list(sys.argv[1:] if argv is None else argv)
    if not command:
        _fail("pass the server command, for example: mediaconductor mcp")

    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    input_text = "\n".join(json.dumps(request) for request in requests) + "\n"
    try:
        proc = subprocess.run(
            command,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _fail(str(exc))
    if proc.returncode != 0:
        _fail(f"server exited {proc.returncode}: {proc.stderr.strip()}")

    responses: dict[int, dict] = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            _fail(f"non-JSON stdout line: {line!r} ({exc})")
        if "id" in response:
            responses[response["id"]] = response

    initialize = responses.get(1, {})
    if "error" in initialize or "result" not in initialize:
        _fail(f"initialize response is invalid: {initialize!r}")
    init_result = initialize["result"]
    if init_result.get("protocolVersion") != "2025-11-25":
        _fail(f"unexpected protocol version: {init_result!r}")
    if init_result.get("serverInfo", {}).get("name") != "media-conductor":
        _fail(f"unexpected server identity: {init_result!r}")

    tools_response = responses.get(2, {})
    tools = tools_response.get("result", {}).get("tools")
    if not isinstance(tools, list) or not tools:
        _fail(f"tools/list returned no tools: {tools_response!r}")
    for tool in tools:
        if not isinstance(tool.get("name"), str) or not isinstance(tool.get("inputSchema"), dict):
            _fail(f"malformed tool descriptor: {tool!r}")

    print(f"MCP smoke test passed: {len(tools)} tools from {' '.join(command)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
