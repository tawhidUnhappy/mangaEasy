"""mangaeasy.cli — the single ``mangaeasy`` entry point.

Every tool in the package is reachable as a subcommand::

    mangaeasy <command> [args...]
    mangaeasy <command> --help
    mangaeasy --help
    mangaeasy --version

Subcommand modules are imported lazily, so ``mangaeasy --help`` stays fast and
never pulls in heavy optional dependencies (torch, opencv, ...) unless
the command you run actually needs them.
"""

from __future__ import annotations

import difflib
import importlib
import sys

from mangaeasy import __version__
from mangaeasy.brand import CLI_NAME, LEGACY_CLI_NAME, PRODUCT_NAME
from mangaeasy.tools.vendored import ensure_vendored_path

# Every existing and future bare-name subprocess call (`"ffmpeg"`, `"uv"`,
# `"git-lfs"`, ...) picks up a vendored copy automatically once this runs —
# see mangaeasy/tools/vendored.py. Pure filesystem check, no network access,
# safe to run unconditionally on every invocation.
ensure_vendored_path()


def _force_utf8_stdio() -> None:
    """Emit UTF-8 regardless of how stdout/stderr are attached.

    On Windows a *piped* stdout defaults to the legacy ANSI code page
    (cp1252), so any command whose output contains a character outside it
    (e.g. the true minus sign in "−14 LUFS" help text) would crash with
    UnicodeEncodeError precisely when run from a script or AI agent — the
    plain-pipe case. Terminals, node-pty/xterm.js, and JSON consumers all
    expect UTF-8 anyway.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


_force_utf8_stdio()

# command name -> (module path, function, group, one-line help)
COMMANDS: dict[str, tuple[str, str, str, str]] = {
    # ── Setup ─────────────────────────────────────────────────────────────────
    "commands":             ("mangaeasy.cli",                                  "commands_main","Setup",           "List every command, or emit the full machine-readable catalog (--json)."),
    "modes":                ("mangaeasy.modes",                                "main",        "Setup",           "List Manga Video, AI Story, and Song Video modes and their isolated dependencies (--json)."),
    "where":                ("mangaeasy.tools.external",                       "where_main",  "Setup",            "Show this install's resolved data/tool paths (--json). Run this first from scripts/AI agents."),
    "library-list":         ("mangaeasy.library_scan",                         "main",        "Setup",            "List projects and per-item readiness under a project root (--json)."),
    "series-plan":          ("mangaeasy.series_plan",                          "plan_main",   "Setup",            "Slice a project into fixed upload batches (default 12/video) and name the next one (--json)."),
    "series-mark-published":("mangaeasy.series_plan",                          "mark_main",   "Setup",            "Record an uploaded batch in publish.json so series-plan advances."),
    "mcp":                  ("mangaeasy.mcp_server",                           "main",        "Setup",            "Run an MCP stdio server exposing MediaConductor as typed tools for AI assistants."),
    "doctor":               ("mangaeasy.tools.install",                        "doctor_main", "Setup",            "Check prerequisites (git/uv/ffmpeg/GPU) and tool status."),
    "setup":                ("mangaeasy.tools.setup",                          "main",        "Setup",            "One-command provisioning: core binaries + AI tool envs + models, GPU-aware (--all / --minimal)."),
    "smoke-test":           ("mangaeasy.tools.smoke",                          "main",        "Setup",            "Prove the install works: build and verify a tiny real video (run after setup)."),
    "install-tool":         ("mangaeasy.tools.install",                        "main",        "Setup",            "Install an external AI tool (index-tts, magi-v3, deepseek-ocr2, z-image-turbo, ...) from GitHub/Hugging Face."),
    "bootstrap-tools":      ("mangaeasy.tools.vendored",                       "bootstrap_main", "Setup",         "Download ffmpeg/uv/git-lfs into this install's own tools dir (the setup step runs this when they're missing)."),

    # ── Background jobs (the right way to run anything long) ─────────────────
    "job-start":            ("mangaeasy.jobs",                                 "start_main",  "Jobs",             "Start a schema-validated long-running tool as a detached job (--tool/--arguments-json; positional CLI compatibility remains)."),
    "job-status":           ("mangaeasy.jobs",                                 "status_main", "Jobs",             "Status/progress/result/log-tail of one background job (--json)."),
    "jobs":                 ("mangaeasy.jobs",                                 "list_main",   "Jobs",             "List background jobs and their states (--json)."),
    "job-run":              ("mangaeasy.jobs",                                 "run_main",    "Jobs",             "(internal) Supervisor spawned by job-start; not for direct use."),

    # ── Multi-agent coordination (see docs/multi-agent.md) ────────────────────
    "work-status":          ("mangaeasy.workboard",                            "status_main", "Multi-agent",      "Per-item pipeline stage from the filesystem + claims + notes; --next lists unclaimed actionable tasks (the resume command)."),
    "work-claim":           ("mangaeasy.workboard",                            "claim_main",  "Multi-agent",      "Atomically claim an item+stage or a shared --resource (e.g. gpu) with a TTL lease so agents never collide."),
    "work-note":            ("mangaeasy.workboard",                            "note_main",   "Multi-agent",      "Append/read the project's shared notebook (characters, speakers, tone, decisions) for agent handoff."),
    "work-qa":              ("mangaeasy.qa_loop",                              "qa_main",     "Multi-agent",      "Aggregated QA gate over crops/narration/audio/renders; every problem carries its fix command — loop until exit 0."),
    "work-artifacts":       ("mangaeasy.qa_loop",                              "artifacts_main", "Multi-agent",   "Inventory of reusable generated artifacts (renders, audio takes, transcripts, sheets, music beds) with reuse hints."),

    # ── General item-based video pipeline (the recommended workflow) ──────────
    "video":                ("mangaeasy.video_pipeline.run_pipeline",          "main",        "Video pipeline",   "Full pipeline: audio (IndexTTS on GPU, Kokoro otherwise), render, join."),
    "video-audio":          ("mangaeasy.video_pipeline.generate_audio",        "main",        "Video pipeline",   "Generate per-item narration audio with Kokoro TTS."),
    "video-audio-indextts": ("mangaeasy.video_pipeline.generate_audio_indextts","main",       "Video pipeline",   "Generate per-item audio with IndexTTS (external env)."),
    "video-render":         ("mangaeasy.video_pipeline.make_videos",           "main",        "Video pipeline",   "Render one video per item from panels + audio."),
    "video-join":           ("mangaeasy.video_pipeline.make_long_video",       "main",        "Video pipeline",   "Join item videos into one long video (optional BGM)."),
    "video-add-bgm":        ("mangaeasy.video_pipeline.add_long_video_bgm",    "main",        "Video pipeline",   "Mix background music into an already-joined long video, without rebuilding it from item clips."),
    "video-check":          ("mangaeasy.video_pipeline.check_items",           "main",        "Video pipeline",   "Validate item inputs (panels + narration.json)."),
    "narration-check":      ("mangaeasy.video_pipeline.narration_check",       "main",        "Video pipeline",   "Validate narration.json/intro.json structure: coverage, dangling images, empty text (--json)."),
    "narration-review-sheets": ("mangaeasy.video_pipeline.narration_sheets",   "main",        "Video pipeline",   "Render panel + narration + OCR sheets for semantic and speaker verification."),
    "narration-edit":       ("mangaeasy.video_pipeline.narration_edit",        "main",        "Video pipeline",   "Upsert/delete/list narration entries from the CLI (optionally pruning stale WAVs)."),
    "video-validate":       ("mangaeasy.video_pipeline.validate_generation",   "main",        "Video pipeline",   "Check generated audio/videos against the inputs."),
    "video-audio-audit":    ("mangaeasy.video_pipeline.audio_audit",          "main",        "Video pipeline",   "Verify every panel has valid, readable audio (catches corrupt/empty files) before rendering; --fix deletes bad ones for regeneration."),
    "video-fade-audio":     ("mangaeasy.video_pipeline.preprocess_audio_fades","main",        "Video pipeline",   "Apply fade in/out to item narration audio."),
    "video-normalize-audio":("mangaeasy.video_pipeline.normalize_long_audio",  "main",        "Video pipeline",   "Loudness-normalize the joined long-video audio."),
    "video-clean-audio":    ("mangaeasy.video_pipeline.cleanup_audio",         "main",        "Video pipeline",   "Clear generated audio for selected items (archived, not lost -- see audio-takes-list)."),
    "video-clean-video":    ("mangaeasy.video_pipeline.cleanup_videos",        "main",        "Video pipeline",   "Delete rendered item videos."),
    "video-clean-work":     ("mangaeasy.video_pipeline.cleanup_work",          "main",        "Video pipeline",   "Delete the work/ scratch directory."),
    "video-clean-all":      ("mangaeasy.video_pipeline.cleanup_all",           "main",        "Video pipeline",   "Delete ALL generated output for a project (audio, videos, archives) in one go -- source chapters are untouched."),
    "audio-takes-list":     ("mangaeasy.video_pipeline.audio_takes",           "list_main",   "Video pipeline",   "List previously archived audio takes (old/run_NNNN/) for a project."),
    "audio-takes-restore":  ("mangaeasy.video_pipeline.audio_takes",           "restore_main","Video pipeline",   "Restore an archived audio take as the active audio instead of regenerating it."),

    # ── YouTube ───────────────────────────────────────────────────────────────
    "youtube-profiles":     ("mangaeasy.youtube.auth",                         "profiles_main","YouTube",         "List isolated YouTube account profiles and cached channels (--json)."),
    "youtube-auth":         ("mangaeasy.youtube.auth",                         "auth_main",   "YouTube",          "Connect a named YouTube account profile (browser consent; default profile: default)."),
    "youtube-status":       ("mangaeasy.youtube.auth",                         "status_main", "YouTube",          "Show one YouTube profile's status (--json); --verify checks it live."),
    "youtube-logout":       ("mangaeasy.youtube.auth",                         "logout_main", "YouTube",          "Disconnect one YouTube profile (delete only that profile's token)."),
    "youtube-upload":       ("mangaeasy.youtube.upload",                       "main",        "YouTube",          "Upload through a selected account profile (resumable; default privacy: private)."),
    "youtube-list":         ("mangaeasy.youtube.list_videos",                  "main",        "YouTube",          "List a selected profile's uploads (id, title, privacy, date)."),
    "youtube-delete":       ("mangaeasy.youtube.delete",                       "main",        "YouTube",          "Delete a video through a selected profile (two-step: requires --confirm)."),
    "youtube-thumbnail":    ("mangaeasy.youtube.thumbnail",                    "main",        "YouTube",          "Set/replace a thumbnail through a selected profile (no re-upload needed)."),

    # ── External AI tool environments ─────────────────────────────────────────
    "tools":                ("mangaeasy.tools.external",                       "main",        "External tools",   "Show where external tool envs (Kokoro/IndexTTS/MAGI/DeepSeek/Z-Image) resolve."),
    "index-tts":            ("mangaeasy.tools.index_tts",                      "main",        "External tools",   "Run IndexTTS inside its external uv env."),
    "deepseek-ocr2":        ("mangaeasy.tools.deepseek_ocr2",                  "main",        "External tools",   "Run DeepSeek-OCR 2 and write `ocr` fields into narration JSON files."),
    "zimage":               ("mangaeasy.tools.zimage",                         "main",        "External tools",   "Generate images with Z-Image Turbo (text-to-image; thumbnails, backgrounds)."),
    "ace-step":             ("mangaeasy.tools.ace_step",                        "main",        "External tools",   "Generate a song with ACE-Step 1.5 inside its isolated uv environment."),
    "demucs":               ("mangaeasy.tools.demucs",                          "main",        "External tools",   "Separate vocals from a song with Demucs inside its isolated uv environment."),
    "whisperx":             ("mangaeasy.tools.whisperx",                        "main",        "External tools",   "Transcribe vocals and align authoritative lyrics with WhisperX timestamps."),

    # AI Story
    "story-init":           ("mangaeasy.story.workflow",                       "init_main",   "AI Story",        "Create a continuity-first AI story project manifest for an agent to complete."),
    "story-check":          ("mangaeasy.story.workflow",                       "check_main",  "AI Story",        "Validate story structure, continuity anchors, scene prompts, narration, and publish metadata (--json)."),
    "story-build":          ("mangaeasy.story.workflow",                       "build_main",  "AI Story",        "Materialize prompts and build scene images, narration video, and optional YouTube upload."),

    # Song & lyrics video
    "song-init":            ("mangaeasy.song.workflow",                        "init_main",   "Song Video",      "Create a song project manifest with lyrics and the minimalistic-sky visual default."),
    "song-check":           ("mangaeasy.song.workflow",                        "check_main",  "Song Video",      "Validate lyrics, generation prompt, alignment inputs, visual prompt, and publish metadata (--json)."),
    "song-build":           ("mangaeasy.song.workflow",                        "build_main",  "Song Video",      "Generate or ingest a song, align corrected lyrics, render a lyric video, and optionally upload."),

    # ── Manga chapter workflow: acquire & crop ────────────────────────────────
    "download":             ("mangaeasy.download.mangadex",                    "main",        "Manga: acquire",   "Download manga chapters from MangaDex (--url + --all for a whole series; polite and resumable)."),
    "style-detect":         ("mangaeasy.panels.style_detect",                  "main",        "Manga: acquire",   "Detect webtoon vs paged manga from page dimensions (--json) to pick the crop tool."),
    "gutter-split":         ("mangaeasy.panels.gutter",                        "main",        "Manga: acquire",   "Split pages along gutters into panels (low-level engine)."),
    "webtoon-split":        ("mangaeasy.panels.webtoon",                       "main",        "Manga: acquire",   "Split webtoon items into panels with auto-split, gap rescue and verify sheets."),
    "webtoon-cutcheck":     ("mangaeasy.panels.cutcheck",                      "main",        "Manga: acquire",   "Render full-res review windows around every forced cut / short panel (crop QA)."),
    "webtoon-override":     ("mangaeasy.panels.overrides_tool",                "main",        "Manga: acquire",   "Add merge/split fixes to an overrides file; indices resolved from the manifest."),
    "panels-remap":         ("mangaeasy.panels.remap",                         "main",        "Manga: acquire",   "After a re-crop, carry narration + audio from the archived old panels to the new ones."),
    "page-split":           ("mangaeasy.panels.page",                          "main",        "Manga: acquire",   "Split paged manga into panels with MAGI v3 detection and verify sheets."),
    "panel-transcript":     ("mangaeasy.ocr.panel_transcript",                 "main",        "Manga: acquire",   "OCR every panel into <item>/transcript.json (grounds narration + speaker attribution)."),

    # ── Image export & AI context ─────────────────────────────────────────────
    "to-pdf":               ("mangaeasy.images.pdf",                           "main",        "Manga: export",    "Export chapter images to a PDF."),
    "to-pdf-lossless":      ("mangaeasy.images.pdf_lossless",                  "main",        "Manga: export",    "Export images to a lossless PDF."),
    "convert-images":       ("mangaeasy.images.convert",                       "main",        "Manga: export",    "Convert / normalize image formats."),
    "thumbnail-compose":    ("mangaeasy.images.thumbnail_compose",             "main",        "Manga: export",    "Compose a YouTube thumbnail: base art + stroked text blocks + border (1280x720)."),
    "watermark":            ("mangaeasy.images.watermark",                     "main",        "Manga: export",    "Apply a text watermark to images."),
    "ai-zip":               ("mangaeasy.images.ai_zip_cli",                    "main",        "Manga: export",    "Pack chapter panels into a labelled ZIP for AI context."),
}


def _group_order() -> list[str]:
    """Groups in first-seen order, so help output stays grouped and stable."""
    order: list[str] = []
    for _, _, group, _ in COMMANDS.values():
        if group not in order:
            order.append(group)
    return order


PRIMARY_COMMANDS = frozenset({
    "modes", "commands", "where", "doctor", "setup", "mcp",
    "job-start", "job-status", "jobs", "video", "download",
    "story-init", "story-check", "story-build",
    "song-init", "song-check", "song-build",
})


def _print_help(stream=None, mode: str | None = None, all_commands: bool = False) -> None:
    # Resolve sys.stdout at call time, not def time — a default bound at
    # import would ignore any redirection set up after this module loads.
    write = (stream or sys.stdout).write
    from mangaeasy.modes import MODES, normalize_mode

    mode = normalize_mode(mode)
    visible = MODES[mode].commands if mode else frozenset(COMMANDS) if all_commands else PRIMARY_COMMANDS
    suffix = f" - {MODES[mode].title} mode" if mode else ""
    write(f"{PRODUCT_NAME} {__version__}{suffix} - agent-native media production\n\n")
    write("Usage:\n")
    write(f"  {CLI_NAME} <command> [args...]\n")
    write(f"  {CLI_NAME} <command> --help     Show a command's own options\n")
    write(f"  {CLI_NAME} commands --mode <mode> --json --full\n")
    write(f"  {CLI_NAME} --version\n")
    write(f"  ({LEGACY_CLI_NAME} remains available as a compatibility alias.)\n\n")

    if mode is None:
        write("Modes:\n")
        for spec in MODES.values():
            write(f"  {spec.key:<14}{spec.description}\n")
        write(f"\nUse `{CLI_NAME} commands --mode <mode>` for a selected catalog, or "
              f"`{CLI_NAME} --help --all` for legacy low-level commands.\n\n")

    width = max(len(name) for name in visible) + 2
    for group in _group_order():
        entries = [(name, details) for name, details in COMMANDS.items()
                   if name in visible and details[2] == group]
        if not entries:
            continue
        write(f"{group}:\n")
        for name, (_, _, _grp, help_text) in entries:
            write(f"  {name:<{width}}{help_text}\n")
        write("\n")


def commands_main() -> int:
    """`mangaeasy commands [--json] [--full]` — the machine-readable catalog.

    Static data straight from COMMANDS (no heavy module imports, so the
    lazy-import design survives). `--full` merges in each command's argument
    schema from mangaeasy/command_spec.py — flags, types, required — so an
    agent can build a command line without running one `--help` per command.
    Commands without a spec entry fall back to `usage` (`<cmd> --help`).
    """
    import argparse
    import json

    from mangaeasy.modes import MODES

    parser = argparse.ArgumentParser(description=f"List {PRODUCT_NAME} commands.")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit the catalog as a single JSON object on stdout.")
    parser.add_argument("--full", action="store_true",
                        help="With --json: include each command's argument schema "
                             "(flags, types, required) and long-running marker.")
    parser.add_argument("--mode", choices=tuple(MODES),
                        help="Return only one mode's commands, keeping agent context small.")
    args = parser.parse_args()

    if args.full:
        from mangaeasy.command_spec import LONG_RUNNING, cli_args_schema

    visible = MODES[args.mode].commands if args.mode else frozenset(COMMANDS)
    catalog = []
    for name, (_, _, group, help_text) in COMMANDS.items():
        if name not in visible:
            continue
        entry: dict = {
            "name": name,
            "group": group,
            "help": help_text,
            "usage": f"{CLI_NAME} {name} --help",
        }
        if args.full:
            entry["long_running"] = name in LONG_RUNNING
            schema = cli_args_schema(name, args.mode)
            if schema is not None:
                entry["args"] = schema
        catalog.append(entry)
    if args.as_json:
        print(json.dumps({"product": PRODUCT_NAME, "version": __version__,
                          "mode": args.mode, "commands": catalog}, ensure_ascii=False))
    else:
        _print_help(mode=args.mode)
    return 0


def _dispatch(command: str, rest: list[str]) -> int:
    module_path, func_name, _, _ = COMMANDS[command]
    module = importlib.import_module(module_path)
    func = getattr(module, func_name)
    # Present the subcommand's own argparse with a sensible prog name and let it
    # parse the remaining args exactly as if it were a standalone tool.
    sys.argv = [f"{CLI_NAME} {command}", *rest]
    try:
        result = func()
    except Exception as exc:
        # Library code raises ConfigError instead of sys.exit; the CLI is the
        # one place that turns it into a clean message + exit 1.
        from mangaeasy.config import ConfigError
        from mangaeasy.path_safety import UnsafePathComponentError

        if isinstance(exc, ConfigError):
            sys.stderr.write(f"[ERROR] {exc}\n")
            return 1
        if isinstance(exc, UnsafePathComponentError):
            sys.stderr.write(f"{CLI_NAME} {command}: error: {exc}\n")
            return 2
        raise
    return result if isinstance(result, int) else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ("-h", "--help", "help"):
        # `mangaeasy help <command>` -> show that command's own help.
        if len(argv) >= 2 and argv[0] == "help" and argv[1] in COMMANDS:
            return _dispatch(argv[1], ["--help"])
        _print_help(all_commands="--all" in argv)
        return 0

    if argv[0] in ("-V", "--version", "version"):
        print(f"{CLI_NAME} {__version__}")
        return 0

    command, rest = argv[0], argv[1:]
    if command not in COMMANDS:
        sys.stderr.write(f"{CLI_NAME}: unknown command '{command}'\n")
        suggestions = difflib.get_close_matches(command, list(COMMANDS), n=3)
        if suggestions:
            sys.stderr.write("Did you mean: " + ", ".join(suggestions) + "?\n")
        sys.stderr.write(f"Run '{CLI_NAME} --help' to list all commands.\n")
        return 2

    return _dispatch(command, rest)


if __name__ == "__main__":
    raise SystemExit(main())
