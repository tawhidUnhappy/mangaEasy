"""`mediaconductor mcp` — a mode-scoped MCP stdio server.

Exposes MediaConductor as typed tools any MCP-capable AI assistant can call.
Pure stdlib: MCP's stdio
transport is newline-delimited JSON-RPC 2.0, so no SDK dependency is needed,
and every tool call shells out to the corresponding `mediaconductor` subcommand
(via `runtime.cli_command`), so the lazy-import design and process isolation
are untouched.

Tool schemas come from mangaeasy/command_spec.py — the single declarative
table shared with `mediaconductor commands --json --full`. Add/change tools there,
not here.

Register from a checkout with, for example,
`claude mcp add mediaconductor -- uv --project <repo> run mediaconductor mcp
--mode ai-story --allow-root <workspace>`, or use the equivalent
command/arguments in another client.

Notes for tool authors: stdout carries ONLY JSON-RPC messages; anything else
goes to stderr. LONG-RUNNING work must go through the `job_start` /
`job_status` tools — a blocking tools/call that runs for minutes to hours
will hit any client's timeout.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import stat
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

from mangaeasy import __version__
from mangaeasy.brand import CLI_NAME, MCP_SERVER_NAME, PRODUCT_NAME
from mangaeasy.command_spec import JSON_COMMANDS, LONG_RUNNING, TOOLS
from mangaeasy.modes import COMMON_TOOLS, MODES, normalize_mode
from mangaeasy.runtime import cli_command, popen_kwargs
from mangaeasy.tools.external import mangaeasy_home

PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = {PROTOCOL_VERSION, "2025-06-18", "2024-11-05"}
MAX_OUTPUT_CHARS = 8000
MAX_REQUEST_CHARS = 1_000_000
MAX_BRIDGED_TEXT_BYTES = 2 * 1024 * 1024

_INLINE_TEXT_BRIDGES = {
    "story_init": ("story", "story_file"),
    "song_init": ("lyrics", "lyrics_file"),
}

# Backwards-compatible alias (tests and external references).
_JSON_COMMANDS = JSON_COMMANDS

_EXCLUSIVE_TOOL_ARGS = {
    "story_init": ("story", "story_file"),
    "story_check": ("manifest", "project_root"),
    "story_build": ("manifest", "project_root"),
    "song_init": ("lyrics", "lyrics_file"),
    "song_check": ("manifest", "project_root"),
    "song_build": ("manifest", "project_root"),
    "youtube_delete": ("video_id", "url"),
}

# Filesystem-bearing MCP properties. Keep this explicit and covered by tests:
# argument names such as ``description`` or ``url`` are strings too, but must
# never be interpreted as paths. Values here are resolved and confined before
# argv is constructed, including when nested under ``job_start``.
_PATH_ARGUMENTS: dict[str, frozenset[str]] = {
    "style_detect": frozenset({"project_root"}),
    "webtoon_split": frozenset({"project_root", "work_dir", "overrides"}),
    "webtoon_cutcheck": frozenset({"project_root", "work_dir"}),
    "webtoon_override": frozenset({"file", "project_root"}),
    "panels_remap": frozenset({"project_root", "audio_root"}),
    "page_split": frozenset({"project_root", "work_dir", "overrides"}),
    "narration_check": frozenset({"project_root"}),
    "panel_transcript": frozenset({"project_root"}),
    "narration_edit": frozenset({"project_root"}),
    "narration_review_sheets": frozenset({"project_root", "work_dir", "output_root"}),
    "thumbnail_compose": frozenset({"base", "output", "spec_json"}),
    "series_plan": frozenset({"project_root"}),
    "series_mark_published": frozenset({"project_root"}),
    "library_list": frozenset({"project_root"}),
    "video_check": frozenset({"project_root", "audio_root"}),
    "video_validate": frozenset({"project_root", "audio_root", "output_root"}),
    "audio_audit": frozenset({"project_root", "audio_root"}),
    "generate_audio": frozenset({"project_root", "audio_root"}),
    "render_videos": frozenset({"project_root", "audio_root", "output_root"}),
    "build_long_video": frozenset({"project_root", "audio_root", "output_root"}),
    "add_bgm": frozenset({"project_root", "output_root", "background_music"}),
    "run_full_pipeline": frozenset({
        "project_root", "audio_root", "output_root", "speaker_wav", "background_music",
    }),
    "youtube_upload": frozenset({"video", "thumbnail"}),
    "youtube_thumbnail": frozenset({"image"}),
    "deepseek_ocr2": frozenset({"project_root"}),
    "work_status": frozenset({"project_root"}),
    "work_claim": frozenset({"project_root"}),
    "work_note": frozenset({"project_root"}),
    "work_qa": frozenset({"project_root"}),
    "work_artifacts": frozenset({"project_root"}),
    "generate_image": frozenset({"output"}),
    "story_init": frozenset({"project_root", "story_file"}),
    "generate_song": frozenset({"lyrics_file", "output"}),
    "separate_vocals": frozenset({"audio", "output_dir"}),
    "align_lyrics": frozenset({"audio", "lyrics_file", "output_dir"}),
    "story_check": frozenset({"manifest", "project_root"}),
    "story_build": frozenset({"manifest", "project_root", "speaker_wav"}),
    "song_init": frozenset({"project_root", "lyrics_file", "audio"}),
    "song_check": frozenset({"manifest", "project_root"}),
    "song_build": frozenset({"manifest", "project_root"}),
}

_RELATIVE_PATH_ARGUMENTS: dict[str, frozenset[str]] = {
    "style_detect": frozenset({"source_subdir"}),
    "webtoon_split": frozenset({"source_subdir"}),
    "webtoon_cutcheck": frozenset({"source_subdir"}),
    "panels_remap": frozenset({"source_subdir"}),
    "page_split": frozenset({"source_subdir"}),
}

_PORTABLE_SEGMENT_ARGUMENTS: dict[str, frozenset[str]] = {
    "download": frozenset({"name"}),
    "video_check": frozenset({"project_name"}),
    "video_validate": frozenset({"project_name"}),
    "audio_audit": frozenset({"project_name"}),
    "generate_audio": frozenset({"project_name"}),
    "render_videos": frozenset({"project_name"}),
    "build_long_video": frozenset({"project_name"}),
    "add_bgm": frozenset({"project_name"}),
    "panels_remap": frozenset({"old_run"}),
}


def _validate_arguments(tool: str, arguments: dict) -> None:
    """Validate the useful JSON Schema subset before constructing argv."""
    _cli_name, _desc, props, required, _flags = TOOLS[tool]
    unknown = sorted(set(arguments) - set(props))
    if unknown:
        raise ValueError(f"unknown argument(s) for {tool}: {', '.join(unknown)}")
    missing = [name for name in required if arguments.get(name) in (None, "", [])]
    if missing:
        raise ValueError(f"missing required argument(s): {', '.join(missing)}")
    if tool in _EXCLUSIVE_TOOL_ARGS:
        left, right = _EXCLUSIVE_TOOL_ARGS[tool]
        if bool(arguments.get(left)) == bool(arguments.get(right)):
            raise ValueError(f"pass exactly one of '{left}' or '{right}'")
    python_types = {
        "string": str, "boolean": bool, "integer": int,
        "number": (int, float), "array": list, "object": dict,
    }
    for name, value in arguments.items():
        if value is None:
            continue
        schema = props[name]
        expected = python_types.get(schema.get("type"))
        if expected is not None and (not isinstance(value, expected) or
                                     schema.get("type") in {"integer", "number"} and isinstance(value, bool)):
            raise ValueError(f"argument '{name}' must be {schema['type']}")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError(f"argument '{name}' must be one of: {', '.join(map(str, schema['enum']))}")
        if "pattern" in schema and isinstance(value, str) and not re.fullmatch(schema["pattern"], value):
            raise ValueError(f"argument '{name}' does not match the required safe format")
        if schema.get("type") == "array" and "items" in schema:
            item_type = python_types.get(schema["items"].get("type"))
            if item_type and any(not isinstance(item, item_type) for item in value):
                raise ValueError(f"every item in argument '{name}' must be {schema['items']['type']}")


def _resolve_allowed_roots(values: list[Path] | None = None) -> tuple[Path, ...]:
    """Canonicalize the explicit MCP workspace policy (cwd by default)."""
    candidates = values or [Path.cwd()]
    roots: list[Path] = []
    for candidate in candidates:
        path = candidate.expanduser().resolve(strict=False)
        if not path.is_dir():
            raise ValueError(f"MCP allow root must be an existing directory: {candidate}")
        if path not in roots:
            roots.append(path)
    return tuple(roots)


def _resolved_user_path(value: str | Path, *, base: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base or Path.cwd()) / path
    return path.resolve(strict=False)


def _path_is_allowed(path: Path, allowed_roots: tuple[Path, ...]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in allowed_roots)


def _require_allowed_path(
    value: str | Path,
    argument_name: str,
    allowed_roots: tuple[Path, ...],
    *,
    base: Path | None = None,
) -> Path:
    try:
        path = _resolved_user_path(value, base=base)
    except (OSError, RuntimeError, ValueError):
        raise ValueError(f"path argument '{argument_name}' could not be resolved safely") from None
    if not _path_is_allowed(path, allowed_roots):
        raise ValueError(
            f"path argument '{argument_name}' resolves outside the MCP --allow-root policy"
        )
    return path


def _validate_relative_path(value: object, argument_name: str, *, one_segment: bool) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"argument '{argument_name}' must be a non-empty relative path")
    portable = value.replace("\\", "/")
    if (
        portable.startswith("/")
        or portable.startswith("//")
        or re.match(r"^[A-Za-z]:", portable)
        or "\x00" in portable
    ):
        raise ValueError(f"argument '{argument_name}' must be a safe relative path")
    parts = portable.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"argument '{argument_name}' must not contain traversal segments")
    if any(
        any(character in '<>:"|?*' or ord(character) < 32 for character in part)
        or part.endswith((".", " "))
        for part in parts
    ):
        raise ValueError(f"argument '{argument_name}' contains non-portable filename characters")
    if one_segment and len(parts) != 1:
        raise ValueError(f"argument '{argument_name}' must be one portable path segment")
    if one_segment and parts[0].split(".", 1)[0].casefold() in {
        "con", "prn", "aux", "nul", "com1", "com2", "com3", "com4", "com5",
        "com6", "com7", "com8", "com9", "lpt1", "lpt2", "lpt3", "lpt4", "lpt5",
        "lpt6", "lpt7", "lpt8", "lpt9",
    }:
        raise ValueError(f"argument '{argument_name}' is a reserved portable filename")


def _manifest_from_arguments(tool: str, arguments: dict) -> Path | None:
    filename = "story.json" if tool.startswith("story_") else "song.json"
    if arguments.get("manifest"):
        return _resolved_user_path(arguments["manifest"])
    if arguments.get("project_root"):
        return (_resolved_user_path(arguments["project_root"]) / filename).resolve(strict=False)
    return None


def _enforce_manifest_paths(
    tool: str,
    arguments: dict,
    allowed_roots: tuple[Path, ...],
) -> None:
    """Confine external paths stored inside Story/Song project manifests."""
    if tool not in {"story_check", "story_build", "song_check", "song_build"}:
        return
    manifest = _manifest_from_arguments(tool, arguments)
    if manifest is None:
        return
    _require_allowed_path(manifest, "manifest", allowed_roots)
    if not manifest.is_file():
        return  # the child command reports a missing manifest without reading elsewhere
    try:
        if manifest.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("manifest exceeds the MCP 16 MB validation limit")
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except ValueError:
        raise
    except OSError:
        raise ValueError("manifest could not be read for MCP workspace validation") from None
    if not isinstance(data, dict):
        return  # normal manifest validation reports the shape
    root = manifest.parent
    if tool.startswith("song_"):
        audio = data.get("audio")
        source = audio.get("source") if isinstance(audio, dict) else None
        if isinstance(source, str) and source:
            _require_allowed_path(source, "audio.source", allowed_roots, base=root)
        render = data.get("render")
        style = render.get("lyrics_style") if isinstance(render, dict) else None
        font_file = style.get("font_file") if isinstance(style, dict) else None
        if isinstance(font_file, str) and font_file and not font_file.startswith("@bundled/"):
            _require_allowed_path(font_file, "render.lyrics_style.font_file", allowed_roots, base=root)
    elif arguments.get("for_publish") or arguments.get("stage") == "publish":
        state_path = root / "review" / "video_generation.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        speaker = state.get("speaker_wav") if isinstance(state, dict) else None
        if isinstance(speaker, str) and speaker:
            _require_allowed_path(speaker, "review.video_generation.speaker_wav", allowed_roots)


def _enforce_workspace_policy(
    tool: str,
    arguments: dict,
    allowed_roots: tuple[Path, ...] | None,
) -> None:
    """Fail closed for every user-controlled filesystem value in one MCP call."""
    if allowed_roots is None:
        return  # internal Python API compatibility; the public server always supplies roots
    for name in _PATH_ARGUMENTS.get(tool, ()):
        value = arguments.get(name)
        if value not in (None, ""):
            _require_allowed_path(value, name, allowed_roots)
    for name in _RELATIVE_PATH_ARGUMENTS.get(tool, ()):
        _validate_relative_path(arguments.get(name), name, one_segment=False)
    for name in _PORTABLE_SEGMENT_ARGUMENTS.get(tool, ()):
        _validate_relative_path(arguments.get(name), name, one_segment=True)

    # Optional CLI roots can be supplied through environment-backed defaults;
    # validate the effective default even though it is not added to argv.
    from mangaeasy.video_pipeline.common import (
        DEFAULT_AUDIO_ROOT,
        DEFAULT_OUTPUT_ROOT,
        DEFAULT_WORK_DIR,
    )
    implicit = {
        "audio_root": DEFAULT_AUDIO_ROOT,
        "output_root": DEFAULT_OUTPUT_ROOT,
        "work_dir": DEFAULT_WORK_DIR,
    }
    props = TOOLS[tool][2]
    for name, default in implicit.items():
        if name in props and arguments.get(name) in (None, ""):
            _require_allowed_path(default, name, allowed_roots)

    uses_configured_story_voice = (
        tool == "story_build"
        and arguments.get("stage", "all") in {"video", "all"}
        and not arguments.get("speaker_wav")
    )
    if tool in {"download", "add_bgm", "run_full_pipeline"} or uses_configured_story_voice:
        from mangaeasy.config import PROJECT_ROOT
        _require_allowed_path(PROJECT_ROOT, "configured workspace", allowed_roots)
    if tool in {"add_bgm", "run_full_pipeline"} and not arguments.get("background_music"):
        if tool != "run_full_pipeline" or not arguments.get("no_background_music"):
            from mangaeasy.defaults import configured_background_music
            _require_allowed_path(
                configured_background_music(), "configured background music", allowed_roots
            )
    if (tool == "run_full_pipeline" or uses_configured_story_voice) and not arguments.get("speaker_wav"):
        from mangaeasy.defaults import default_speaker_wav
        speaker = default_speaker_wav()
        if speaker.is_file():
            _require_allowed_path(speaker, "configured speaker WAV", allowed_roots)
    _enforce_manifest_paths(tool, arguments, allowed_roots)


def _build_args(tool: str, arguments: dict) -> list[str]:
    cli_name, _desc, props, required, flags = TOOLS[tool]
    _validate_arguments(tool, arguments)
    args: list[str] = []
    # Iterate in SPEC order, not client-dict order: positionals (e.g.
    # job_start's command + args) must land in the argv position the spec
    # defines, regardless of the JSON key order the client happened to send.
    for prop, (flag, kind) in flags.items():
        value = arguments.get(prop)
        if value is None:
            continue
        if kind == "positional":
            args.append(str(value))
        elif kind == "positional-list":
            args.extend(str(v) for v in value or [])
        elif kind == "flag":
            if value:
                args.append(flag)
        elif kind == "no-flag":
            if value is False:
                args.append(flag)
        elif kind == "list":
            if value:
                args.extend([flag, *[str(v) for v in value]])
        elif kind == "repeat":
            for v in value or []:
                args.extend([flag, str(v)])
        elif kind == "json":
            args.extend([flag, json.dumps(value, ensure_ascii=False, separators=(",", ":"))])
        else:  # value
            args.extend([flag, str(value)])
    if cli_name in JSON_COMMANDS:
        args.append("--json")
    return args


def _is_link_or_reparse(path: Path) -> bool:
    try:
        file_stat = path.lstat()
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(
        reparse_flag and getattr(file_stat, "st_file_attributes", 0) & reparse_flag
    )


def _managed_mcp_temp_dir() -> Path:
    """Create the private app-managed directory used for transient MCP text."""
    home = mangaeasy_home()
    temporary_parent = home / "tmp"
    root = temporary_parent / "mcp"
    if _is_link_or_reparse(temporary_parent) or _is_link_or_reparse(root):
        raise ValueError("the managed MCP temporary directory is unsafe")
    temporary_parent.mkdir(parents=True, exist_ok=True)
    if _is_link_or_reparse(temporary_parent):
        raise ValueError("the managed MCP temporary directory is unsafe")
    root.mkdir(parents=True, exist_ok=True)
    if _is_link_or_reparse(root) or not root.resolve().is_relative_to(home.resolve()):
        raise ValueError("the managed MCP temporary directory is unsafe")
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


@contextmanager
def _bridge_inline_text(tool: str, arguments: dict):
    """Convert large MCP story/lyrics text into a private temporary file.

    The original typed arguments are validated before transformation. The
    resulting file exists only for the duration of the child process and is
    always removed, including when argv construction or execution fails.
    """
    _validate_arguments(tool, arguments)
    bridged = dict(arguments)
    temporary_paths: list[Path] = []
    try:
        mapping = _INLINE_TEXT_BRIDGES.get(tool)
        if mapping:
            inline_name, file_name = mapping
            if inline_name in bridged and bridged[inline_name] is not None:
                try:
                    encoded = bridged[inline_name].encode("utf-8")
                except UnicodeEncodeError:
                    raise ValueError(
                        f"argument '{inline_name}' must be valid UTF-8 text"
                    ) from None
                if len(encoded) > MAX_BRIDGED_TEXT_BYTES:
                    raise ValueError(
                        f"argument '{inline_name}' exceeds the "
                        f"{MAX_BRIDGED_TEXT_BYTES}-byte MCP text limit"
                    )
                root = _managed_mcp_temp_dir()
                with NamedTemporaryFile(
                    "wb", dir=root, prefix=".input-", suffix=".txt", delete=False
                ) as handle:
                    path = Path(handle.name)
                    temporary_paths.append(path)
                    handle.write(encoded)
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
                del bridged[inline_name]
                bridged[file_name] = str(path)
        yield bridged, tuple(temporary_paths)
    finally:
        for path in temporary_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _redact_temp_paths(text: str, paths: tuple[Path, ...]) -> str:
    """Hide internal bridge paths if a child happens to echo them."""
    redacted = text
    for path in paths:
        raw = str(path)
        variants = {raw, path.as_posix(), json.dumps(raw, ensure_ascii=False)[1:-1]}
        for value in variants:
            if value:
                redacted = redacted.replace(value, "<mcp-temp-file>")
    return redacted


def _clip(text: str, limit: int) -> str:
    """Head+tail truncation: errors usually sit at one end or the other."""
    if len(text) <= limit:
        return text
    head = limit // 4
    tail = limit - head
    return text[:head] + f"\n... [{len(text) - limit} chars omitted] ...\n" + text[-tail:]


def _parse_json_report(stdout: str) -> dict | list | None:
    """The report object of a --json command, scanning from the last line up.

    The contract says exactly one JSON object on stdout, but a stray print
    from a dependency after it must not blind the parser — so walk backwards
    to the first parseable JSON line instead of trusting line ordering.
    """
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if not (line.startswith("{") or line.startswith("[")):
            continue
        try:
            return json.loads(line)
        except ValueError:
            continue
    return None


def _bounded_json_value(value, limit: int = MAX_OUTPUT_CHARS):
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    if len(encoded) <= limit:
        return value
    return {"truncated": True, "original_chars": len(encoded), "preview": _clip(encoded, limit)}


def _allowed_tools(mode: str | None, all_tools: bool = False) -> frozenset[str]:
    if all_tools:
        return frozenset(TOOLS)
    if mode:
        return MODES[mode].tools & frozenset(TOOLS)
    return COMMON_TOOLS & frozenset(TOOLS)


def _check_mode_access(tool: str, arguments: dict, mode: str | None, all_tools: bool) -> None:
    if tool not in _allowed_tools(mode, all_tools):
        label = mode or "router"
        raise ValueError(f"tool '{tool}' is not available in MCP mode '{label}'")
    if mode and tool in {"setup", "doctor"} and arguments.get("mode") not in {None, mode}:
        raise ValueError(f"{tool} cannot cross from MCP mode '{mode}' to '{arguments.get('mode')}'")
    if mode and tool == "install_tool":
        from mangaeasy.tools.setup import MODE_TOOLS
        if arguments.get("name") not in MODE_TOOLS[mode]:
            raise ValueError(f"tool '{arguments.get('name')}' is outside MCP mode '{mode}'")
    if tool == "job_start" and not all_tools:
        target = str(arguments.get("tool", ""))
        if target not in _allowed_tools(mode, all_tools):
            raise ValueError(
                f"job_start tool '{target}' is outside MCP mode '{mode or 'router'}'"
            )


def _run_tool(
    tool: str,
    arguments: dict,
    mode: str | None = None,
    all_tools: bool = False,
    allowed_roots: tuple[Path, ...] | None = None,
) -> tuple[str, bool]:
    """Run the tool's CLI command; returns (text content, is_error)."""
    _check_mode_access(tool, arguments, mode, all_tools)
    cli_name = TOOLS[tool][0]
    arguments = dict(arguments)
    if mode and tool in {"setup", "doctor"}:
        arguments.setdefault("mode", mode)
    _validate_arguments(tool, arguments)
    _enforce_workspace_policy(tool, arguments, allowed_roots)
    argument_names = sorted(arguments)
    with _bridge_inline_text(tool, arguments) as (bridged_arguments, temporary_paths):
        if tool == "job_start":
            target = str(bridged_arguments["tool"])
            if target not in TOOLS or target == "job_start":
                raise ValueError(f"unknown or recursive background tool: {target}")
            target_raw = bridged_arguments.get("arguments") or {}
            if not isinstance(target_raw, dict):
                raise ValueError("job_start argument 'arguments' must be object")
            target_arguments = dict(target_raw)
            _check_mode_access(target, target_arguments, mode, all_tools)
            if mode and target in {"setup", "doctor"}:
                target_arguments.setdefault("mode", mode)
            if TOOLS[target][0] not in LONG_RUNNING:
                raise ValueError(f"tool '{target}' is not marked long-running; call it directly")
            _validate_arguments(target, target_arguments)
            _enforce_workspace_policy(target, target_arguments, allowed_roots)
            argv = cli_command(
                cli_name,
                "--tool", target,
                "--arguments-json", json.dumps(
                    target_arguments, ensure_ascii=False, separators=(",", ":")
                ),
            )
        else:
            argv = cli_command(cli_name, *_build_args(tool, bridged_arguments))
        names_label = ",".join(argument_names) if argument_names else "none"
        print(
            f"[mcp] run tool={tool} argument_names={names_label}",
            file=sys.stderr,
            flush=True,
        )
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            **popen_kwargs(),
        )
        stdout = _redact_temp_paths(proc.stdout or "", temporary_paths)
        stderr = _redact_temp_paths(proc.stderr or "", temporary_paths).strip()

    result_payload = None
    for line in stdout.splitlines():
        if line.startswith("MANGAEASY_RESULT "):
            try:
                result_payload = json.loads(line[len("MANGAEASY_RESULT "):])
            except ValueError:
                pass

    body: dict = {"exit_code": proc.returncode}
    if result_payload is not None:
        body["result"] = _bounded_json_value(result_payload)
    if cli_name in JSON_COMMANDS:
        report = _parse_json_report(stdout)
        if report is not None:
            body["report"] = _bounded_json_value(report)
        else:
            body["output"] = _clip(stdout, MAX_OUTPUT_CHARS)
    else:
        body["output"] = _clip(stdout, MAX_OUTPUT_CHARS)
    if stderr:
        body["stderr"] = _clip(stderr, 2000)
    return json.dumps(body, ensure_ascii=False, indent=2), proc.returncode != 0


def _tools_list(mode: str | None = None, all_tools: bool = False) -> list[dict]:
    allowed = _allowed_tools(mode, all_tools)
    result = []
    for name, (_cli, desc, props, required, _flags) in TOOLS.items():
        if name not in allowed:
            continue
        scoped_props = copy.deepcopy(props)
        if mode and name == "install_tool":
            from mangaeasy.tools.setup import MODE_TOOLS
            scoped_props["name"]["enum"] = MODE_TOOLS[mode]
        if mode and name in {"setup", "doctor"}:
            scoped_props["mode"]["enum"] = [mode]
            scoped_props["mode"]["default"] = mode
        if name == "job_start":
            targets = sorted(
                target for target in allowed
                if target != "job_start" and TOOLS[target][0] in LONG_RUNNING
            )
            scoped_props["tool"]["enum"] = targets
        result.append({
            "name": name,
            "description": desc,
            "inputSchema": {"type": "object", "properties": scoped_props, "required": required,
                            "additionalProperties": False},
        })
        if name in _EXCLUSIVE_TOOL_ARGS:
            left, right = _EXCLUSIVE_TOOL_ARGS[name]
            result[-1]["inputSchema"]["oneOf"] = [
                {"required": [left]},
                {"required": [right]},
            ]
    return result


def _reply(msg_id, result=None, error=None) -> None:
    response: dict = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        response["error"] = error
    else:
        response["result"] = result
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _handle(
    msg: dict,
    mode: str | None = None,
    all_tools: bool = False,
    allowed_roots: tuple[Path, ...] | None = None,
) -> None:
    method = msg.get("method")
    msg_id = msg.get("id")
    if "params" not in msg:
        params = {}
    else:
        raw_params = msg["params"]
        if not isinstance(raw_params, dict):
            _reply(msg_id, error={"code": -32602, "message": "params must be an object"})
            return
        params = raw_params

    if method == "initialize":
        requested_version = params.get("protocolVersion") or PROTOCOL_VERSION
        client_version = requested_version if requested_version in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        _reply(msg_id, {
            "protocolVersion": client_version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": MCP_SERVER_NAME, "version": __version__},
            "instructions": (
                f"{PRODUCT_NAME} MCP in {mode or 'router'} mode. "
                "Run long operations through job_start and poll job_status. "
                "Filesystem arguments are confined to the server's --allow-root policy."
            ),
        })
        return
    if msg_id is None:
        return  # notification (e.g. notifications/initialized) — nothing to answer
    if method == "ping":
        _reply(msg_id, {})
        return
    if method == "tools/list":
        _reply(msg_id, {"tools": _tools_list(mode, all_tools)})
        return
    if method == "tools/call":
        tool = params.get("name")
        if tool not in TOOLS:
            _reply(msg_id, error={"code": -32602, "message": f"unknown tool: {tool}"})
            return
        if "arguments" not in params:
            tool_arguments = {}
        else:
            raw_arguments = params["arguments"]
            if not isinstance(raw_arguments, dict):
                _reply(msg_id, error={"code": -32602, "message": "arguments must be an object"})
                return
            tool_arguments = raw_arguments
        try:
            text, is_error = _run_tool(
                tool, tool_arguments, mode, all_tools, allowed_roots
            )
        except ValueError as exc:
            _reply(msg_id, error={"code": -32602, "message": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001 — must never crash the server loop
            text = json.dumps({"error": f"tool execution failed ({type(exc).__name__})"})
            is_error = True
        _reply(msg_id, {"content": [{"type": "text", "text": text}], "isError": is_error})
        return
    _reply(msg_id, error={"code": -32601, "message": f"method not found: {method}"})


def main() -> int:
    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} mcp",
        description="Run the MediaConductor MCP stdio server with a mode-scoped tool catalog.",
    )
    parser.add_argument("--mode",
                        help="Expose only one production mode. Omit for the small router catalog.")
    parser.add_argument("--all-tools", action="store_true",
                        help="Compatibility escape hatch: expose every tool (large context cost).")
    parser.add_argument(
        "--allow-root",
        action="append",
        type=Path,
        default=[],
        metavar="PATH",
        help=(
            "Permit MCP filesystem arguments only below this existing directory "
            "(repeatable; default: current working directory)."
        ),
    )
    args = parser.parse_args()
    try:
        mode = normalize_mode(args.mode)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        allowed_roots = _resolve_allowed_roots(args.allow_root)
    except ValueError as exc:
        parser.error(str(exc))
    label = "all-tools" if args.all_tools else mode or "router"
    print(
          f"[mcp] {PRODUCT_NAME} {__version__} MCP server on stdio "
          f"(mode={label}, allowed_roots={len(allowed_roots)})",
          file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if len(line) > MAX_REQUEST_CHARS:
            _reply(None, error={"code": -32600, "message": "request exceeds 1,000,000 characters"})
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            _reply(None, error={"code": -32700, "message": "parse error"})
            continue
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            _reply(msg.get("id") if isinstance(msg, dict) else None,
                   error={"code": -32600, "message": "invalid JSON-RPC request"})
            continue
        try:
            _handle(msg, mode, args.all_tools, allowed_roots)
        except Exception as exc:  # noqa: BLE001 — keep serving without leaking values
            print(f"[mcp] handler error type={type(exc).__name__}", file=sys.stderr, flush=True)
            _reply(msg.get("id"), error={"code": -32603, "message": "internal error"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
