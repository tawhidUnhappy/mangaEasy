"""`mangaeasy mcp` — an MCP (Model Context Protocol) stdio server.

Exposes the mangaEasy pipeline as typed tools any MCP-capable AI assistant
(Claude Code/Desktop, Cursor, ...) can call. Pure stdlib: MCP's stdio
transport is newline-delimited JSON-RPC 2.0, so no SDK dependency is needed,
and every tool call shells out to the corresponding `mangaeasy` subcommand
(via `runtime.cli_command`), so the lazy-import design and process isolation
are untouched.

Register with e.g. `claude mcp add mangaeasy -- mangaeasy mcp`, or in any
client's config: command `mangaeasy`, args `["mcp"]`.

Notes for tool authors: stdout carries ONLY JSON-RPC messages; anything else
goes to stderr. Long jobs (audio generation, tool installs) block the call
until they finish — that is expected MCP behaviour.
"""

from __future__ import annotations

import json
import subprocess
import sys

from mangaeasy import __version__
from mangaeasy.runtime import cli_command, popen_kwargs

PROTOCOL_VERSION = "2024-11-05"
MAX_OUTPUT_CHARS = 8000

_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_INT = {"type": "integer"}
_NUM = {"type": "number"}
_ITEMS = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Item/chapter folder names or ranges, e.g. [\"01\", \"05-08\"]. Omit for all.",
}
_PROJECT_ROOT = {
    "type": "string",
    "description": "Absolute path to the folder containing the item folders (usually library/<project>).",
}

# name -> (cli command, description, {property: schema}, [required], {property: flag spec})
# Flag spec kinds: "value" (--flag VALUE), "flag" (--flag when true),
# "no-flag" (--no-flag when false), "list" (--flag V1 V2 ...).
TOOLS: dict[str, tuple[str, str, dict, list[str], dict]] = {
    "doctor": (
        "doctor",
        "Check this machine: ffmpeg/uv/git presence, GPU backend (cuda/mps/cpu), installed AI tools.",
        {"check_updates": {**_BOOL, "description": "Also check installed AI tools for upstream updates."}},
        [],
        {"check_updates": ("--check-updates", "flag")},
    ),
    "where": (
        "where",
        "Show this install's resolved paths (data root, tools home) and version. Run this first.",
        {}, [], {},
    ),
    "library_list": (
        "library-list",
        "List projects and per-item readiness (panels/narration/intro) under a project root. Read-only.",
        {"project_root": {**_STR, "description": "Folder whose library/ gets scanned."}},
        ["project_root"],
        {"project_root": ("--project-root", "value")},
    ),
    "video_check": (
        "video-check",
        "Validate item inputs before generation: narration vs panels vs audio counts and name matches.",
        {"project_root": _PROJECT_ROOT, "audio_root": _STR, "project_name": _STR, "items": _ITEMS},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "audio_root": ("--audio-root", "value"),
         "project_name": ("--project-name", "value"), "items": ("--items", "list")},
    ),
    "video_validate": (
        "video-validate",
        "Validate generated audio/videos against the inputs (stream formats, durations, counts).",
        {"project_root": _PROJECT_ROOT, "audio_root": _STR, "output_root": _STR,
         "project_name": _STR, "items": _ITEMS,
         "require_long": {**_BOOL, "description": "Also require/validate the joined long video (default true)."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "audio_root": ("--audio-root", "value"),
         "output_root": ("--output-root", "value"), "project_name": ("--project-name", "value"),
         "items": ("--items", "list"), "require_long": ("--no-require-long", "no-flag")},
    ),
    "audio_audit": (
        "video-audio-audit",
        "ffprobe every expected narration audio file; report missing panels vs missing/corrupt audio. "
        "Set fix=true to delete bad audio so the next generation run recreates exactly those.",
        {"project_root": _PROJECT_ROOT, "audio_root": _STR, "project_name": _STR, "items": _ITEMS,
         "fix": _BOOL},
        ["project_root", "audio_root"],
        {"project_root": ("--project-root", "value"), "audio_root": ("--audio-root", "value"),
         "project_name": ("--project-name", "value"), "items": ("--items", "list"),
         "fix": ("--fix", "flag")},
    ),
    "generate_audio": (
        "video-audio",
        "Generate per-panel narration audio with Kokoro TTS (CPU-friendly). LONG-RUNNING. "
        "Existing audio is skipped unless overwrite=true (old takes are archived, never lost).",
        {"project_root": _PROJECT_ROOT, "audio_root": _STR, "project_name": _STR, "items": _ITEMS,
         "overwrite": _BOOL},
        ["project_root", "audio_root"],
        {"project_root": ("--project-root", "value"), "audio_root": ("--audio-root", "value"),
         "project_name": ("--project-name", "value"), "items": ("--items", "list"),
         "overwrite": ("--overwrite", "flag")},
    ),
    "render_videos": (
        "video-render",
        "Render one video per item from panels + audio. Needs audio to exist (run generate_audio/audio_audit first).",
        {"project_root": _PROJECT_ROOT, "audio_root": _STR, "output_root": _STR,
         "project_name": _STR, "items": _ITEMS, "overwrite": _BOOL},
        ["project_root", "audio_root", "output_root"],
        {"project_root": ("--project-root", "value"), "audio_root": ("--audio-root", "value"),
         "output_root": ("--output-root", "value"), "project_name": ("--project-name", "value"),
         "items": ("--items", "list"), "overwrite": ("--overwrite", "flag")},
    ),
    "build_long_video": (
        "video-join",
        "Join rendered item videos into one long video (no background music — use add_bgm afterward).",
        {"project_root": _PROJECT_ROOT, "audio_root": _STR, "output_root": _STR,
         "project_name": _STR, "items": _ITEMS, "overwrite": _BOOL},
        ["project_root", "output_root"],
        {"project_root": ("--project-root", "value"), "audio_root": ("--audio-root", "value"),
         "output_root": ("--output-root", "value"), "project_name": ("--project-name", "value"),
         "items": ("--items", "list"), "overwrite": ("--overwrite", "flag")},
    ),
    "add_bgm": (
        "video-add-bgm",
        "Mix background music into the already-joined long video (cheap — no re-join). "
        "Writes a new timestamped file unless replace=true.",
        {"project_root": _PROJECT_ROOT, "output_root": _STR,
         "background_music": {**_STR, "description": "Absolute path to the music file."},
         "music_volume_db": {**_NUM, "description": "Music loudness in dB, negative = quieter (default -25)."},
         "project_name": _STR, "replace": _BOOL},
        ["project_root", "output_root", "background_music"],
        {"project_root": ("--project-root", "value"), "output_root": ("--output-root", "value"),
         "background_music": ("--background-music", "value"),
         "music_volume_db": ("--music-volume-db", "value"), "project_name": ("--project-name", "value"),
         "replace": ("--replace", "flag")},
    ),
    "run_full_pipeline": (
        "video",
        "The all-in-one pipeline: audio -> render -> optional join/normalize/BGM. VERY LONG-RUNNING. "
        "Prefer the single-step tools when iterating.",
        {"project_root": _PROJECT_ROOT, "audio_root": _STR, "output_root": _STR, "items": _ITEMS,
         "tts": {"type": "string", "enum": ["auto", "kokoro", "indextts"]},
         "build_long_video": _BOOL,
         "background_music": _STR,
         "music_volume_db": _NUM},
        ["project_root", "audio_root", "output_root"],
        {"project_root": ("--project-root", "value"), "audio_root": ("--audio-root", "value"),
         "output_root": ("--output-root", "value"), "items": ("--items", "list"),
         "tts": ("--tts", "value"), "build_long_video": ("--build-long-video", "flag"),
         "background_music": ("--background-music", "value"),
         "music_volume_db": ("--music-volume-db", "value")},
    ),
    "youtube_status": (
        "youtube-status",
        "YouTube connection status: connected or not, channel name. Set verify=true for a live "
        "check (token refresh + channel query; needs network). Connecting itself needs a human "
        "in a browser — tell the user to run `mangaeasy youtube-auth` (see docs/youtube.md).",
        {"verify": {**_BOOL, "description": "Also verify the token works right now (network call)."}},
        [],
        {"verify": ("--verify", "flag")},
    ),
    "youtube_upload": (
        "youtube-upload",
        "Upload a video to the connected YouTube channel (resumable, LONG-RUNNING). Requires a prior "
        "`mangaeasy youtube-auth` by the user. Default privacy is private (YouTube forces private for "
        "unaudited API projects); one upload costs 1,600 of the default 10,000/day quota units.",
        {"video": {**_STR, "description": "Absolute path to the video file."},
         "title": {**_STR, "description": "Video title (max 100 chars)."},
         "description": _STR,
         "tags": {**_STR, "description": "Comma-separated tags, e.g. 'manga,recap'."},
         "privacy": {"type": "string", "enum": ["private", "unlisted", "public"]}},
        ["video", "title"],
        {"video": ("--video", "value"), "title": ("--title", "value"),
         "description": ("--description", "value"), "tags": ("--tags", "value"),
         "privacy": ("--privacy", "value")},
    ),
    "bootstrap_tools": (
        "bootstrap-tools",
        "Download ffmpeg/uv/git-lfs (~100 MB, one-time) into this install's own tools dir. LONG-RUNNING.",
        {}, [], {},
    ),
    "install_tool": (
        "install-tool",
        "Install an external AI tool env (multi-GB download). LONG-RUNNING.",
        {"name": {"type": "string", "enum": ["kokoro-82m", "index-tts", "magi-v3", "got-ocr2", "z-image-turbo"]},
         "update": _BOOL},
        ["name"],
        {"name": (None, "positional"), "update": ("--update", "flag")},
    ),
    "generate_image": (
        "zimage",
        "Generate images with Z-Image Turbo (text-to-image). LONG-RUNNING on first call "
        "(model load ~1-2 min; then ~10-30 s per image on a GPU). Requires "
        "`mangaeasy install-tool z-image-turbo` first. Long descriptive prompts work best.",
        {"prompt": {**_STR, "description": "Text prompt (English or Chinese)."},
         "output": {**_STR, "description": "Absolute output PNG path."},
         "width": _INT, "height": _INT,
         "count": {**_INT, "description": "Number of variants (files get _01.._NN suffixes)."},
         "seed": _INT},
        ["prompt", "output"],
        {"prompt": ("--prompt", "value"), "output": ("--output", "value"),
         "width": ("--width", "value"), "height": ("--height", "value"),
         "count": ("--count", "value"), "seed": ("--seed", "value")},
    ),
}

# Commands whose --json flag should be appended automatically.
_JSON_COMMANDS = {"doctor", "where", "library-list", "video-check", "video-validate",
                  "video-audio-audit", "youtube-status", "youtube-upload"}


def _build_args(tool: str, arguments: dict) -> list[str]:
    cli_name, _desc, props, required, flags = TOOLS[tool]
    missing = [name for name in required if arguments.get(name) in (None, "", [])]
    if missing:
        raise ValueError(f"missing required argument(s): {', '.join(missing)}")
    args: list[str] = []
    for prop, value in arguments.items():
        if prop not in flags or value is None:
            continue
        flag, kind = flags[prop]
        if kind == "positional":
            args.append(str(value))
        elif kind == "flag":
            if value:
                args.append(flag)
        elif kind == "no-flag":
            if value is False:
                args.append(flag)
        elif kind == "list":
            if value:
                args.extend([flag, *[str(v) for v in value]])
        else:  # value
            args.extend([flag, str(value)])
    if cli_name in _JSON_COMMANDS:
        args.append("--json")
    return args


def _run_tool(tool: str, arguments: dict) -> tuple[str, bool]:
    """Run the tool's CLI command; returns (text content, is_error)."""
    cli_name = TOOLS[tool][0]
    argv = cli_command(cli_name, *_build_args(tool, arguments))
    print(f"[mcp] run: {' '.join(argv)}", file=sys.stderr, flush=True)
    proc = subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", errors="replace", **popen_kwargs()
    )
    stdout = proc.stdout or ""
    stderr = (proc.stderr or "").strip()

    result_payload = None
    for line in stdout.splitlines():
        if line.startswith("MANGAEASY_RESULT "):
            try:
                result_payload = json.loads(line[len("MANGAEASY_RESULT "):])
            except ValueError:
                pass

    body: dict = {"exit_code": proc.returncode}
    if result_payload is not None:
        body["result"] = result_payload
    # JSON-mode commands print exactly one JSON object — pass it through parsed.
    if TOOLS[tool][0] in _JSON_COMMANDS:
        try:
            body["report"] = json.loads(stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            body["output"] = stdout[-MAX_OUTPUT_CHARS:]
    else:
        body["output"] = stdout[-MAX_OUTPUT_CHARS:]
    if stderr:
        body["stderr"] = stderr[-2000:]
    return json.dumps(body, ensure_ascii=False, indent=2), proc.returncode != 0


def _tools_list() -> list[dict]:
    return [
        {
            "name": name,
            "description": desc,
            "inputSchema": {"type": "object", "properties": props, "required": required},
        }
        for name, (_cli, desc, props, required, _flags) in TOOLS.items()
    ]


def _reply(msg_id, result=None, error=None) -> None:
    response: dict = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        response["error"] = error
    else:
        response["result"] = result
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _handle(msg: dict) -> None:
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        client_version = params.get("protocolVersion") or PROTOCOL_VERSION
        _reply(msg_id, {
            "protocolVersion": client_version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mangaeasy", "version": __version__},
        })
        return
    if msg_id is None:
        return  # notification (e.g. notifications/initialized) — nothing to answer
    if method == "ping":
        _reply(msg_id, {})
        return
    if method == "tools/list":
        _reply(msg_id, {"tools": _tools_list()})
        return
    if method == "tools/call":
        tool = params.get("name")
        if tool not in TOOLS:
            _reply(msg_id, error={"code": -32602, "message": f"unknown tool: {tool}"})
            return
        try:
            text, is_error = _run_tool(tool, params.get("arguments") or {})
        except ValueError as exc:
            _reply(msg_id, error={"code": -32602, "message": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001 — must never crash the server loop
            text, is_error = json.dumps({"error": str(exc)}), True
        _reply(msg_id, {"content": [{"type": "text", "text": text}], "isError": is_error})
        return
    _reply(msg_id, error={"code": -32601, "message": f"method not found: {method}"})


def main() -> int:
    print(f"[mcp] mangaeasy {__version__} MCP server on stdio", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        try:
            _handle(msg)
        except Exception as exc:  # noqa: BLE001 — keep serving
            print(f"[mcp] handler error: {exc}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
