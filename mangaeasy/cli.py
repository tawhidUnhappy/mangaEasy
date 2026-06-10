"""mangaeasy.cli — the single ``mangaeasy`` entry point.

Every tool in the package is reachable as a subcommand::

    mangaeasy <command> [args...]
    mangaeasy <command> --help
    mangaeasy --help
    mangaeasy --version

Subcommand modules are imported lazily, so ``mangaeasy --help`` stays fast and
never pulls in heavy optional dependencies (torch, opencv, flask, ...) unless
the command you run actually needs them.
"""

from __future__ import annotations

import difflib
import importlib
import sys

from mangaeasy import __version__

# command name -> (module path, function, group, one-line help)
COMMANDS: dict[str, tuple[str, str, str, str]] = {
    # ── General item-based video pipeline (the recommended workflow) ──────────
    "video":                ("mangaeasy.video_pipeline.run_pipeline",          "main",        "Video pipeline",   "Full pipeline: generate audio, render item videos, optionally join."),
    "video-audio":          ("mangaeasy.video_pipeline.generate_audio",        "main",        "Video pipeline",   "Generate per-item narration audio with Kokoro TTS."),
    "video-audio-indextts": ("mangaeasy.video_pipeline.generate_audio_indextts","main",       "Video pipeline",   "Generate per-item audio with IndexTTS (external env)."),
    "video-audio-f5tts":    ("mangaeasy.video_pipeline.generate_audio_f5tts",  "main",        "Video pipeline",   "Generate per-item audio with F5-TTS (external env)."),
    "video-render":         ("mangaeasy.video_pipeline.make_videos",           "main",        "Video pipeline",   "Render one video per item from panels + audio."),
    "video-join":           ("mangaeasy.video_pipeline.make_long_video",       "main",        "Video pipeline",   "Join item videos into one long video (optional BGM)."),
    "video-check":          ("mangaeasy.video_pipeline.check_items",           "main",        "Video pipeline",   "Validate item inputs (panels + narration.json)."),
    "video-validate":       ("mangaeasy.video_pipeline.validate_generation",   "main",        "Video pipeline",   "Check generated audio/videos against the inputs."),
    "video-fade-audio":     ("mangaeasy.video_pipeline.preprocess_audio_fades","main",        "Video pipeline",   "Apply fade in/out to item narration audio."),
    "video-normalize-audio":("mangaeasy.video_pipeline.normalize_long_audio",  "main",        "Video pipeline",   "Loudness-normalize the joined long-video audio."),
    "video-clean-audio":    ("mangaeasy.video_pipeline.cleanup_audio",         "main",        "Video pipeline",   "Delete generated audio for selected items."),
    "video-clean-video":    ("mangaeasy.video_pipeline.cleanup_videos",        "main",        "Video pipeline",   "Delete rendered item videos."),
    "video-clean-work":     ("mangaeasy.video_pipeline.cleanup_work",          "main",        "Video pipeline",   "Delete the work/ scratch directory."),

    # ── External AI tool environments ─────────────────────────────────────────
    "tools":                ("mangaeasy.tools.external",                       "main",        "External tools",   "Show where external tool envs (Kokoro/IndexTTS/F5/MAGI) resolve."),
    "index-tts":            ("mangaeasy.tools.index_tts",                      "main",        "External tools",   "Run IndexTTS inside its external uv env."),
    "f5-tts":               ("mangaeasy.tools.f5_tts",                         "main",        "External tools",   "Run F5-TTS inside its external uv env."),

    # ── Manga chapter workflow: acquire & edit ────────────────────────────────
    "download":             ("mangaeasy.download.mangadex",                    "main",        "Manga: acquire",   "Download a manga chapter from MangaDex."),
    "cut-page":             ("mangaeasy.web.cut_page",                         "main",        "Manga: acquire",   "Web editor: cut full pages into panels."),
    "panel-editor":         ("mangaeasy.web.panel_editor",                     "main",        "Manga: acquire",   "Web editor: arrange panels (vertical manhwa)."),
    "gutter-split":         ("mangaeasy.panels.gutter",                        "main",        "Manga: acquire",   "Split pages along gutters into panels."),
    "process-panels":       ("mangaeasy.panels.process",                       "main",        "Manga: acquire",   "Post-process panels (upscale / mirror / clean bubbles)."),

    # ── Manga chapter workflow: narration ─────────────────────────────────────
    "narration-editor":     ("mangaeasy.web.narration_editor",                 "main",        "Manga: narration", "Web editor: write narration for one chapter."),
    "narration-editor-all": ("mangaeasy.web.narration_editor_all",             "main",        "Manga: narration", "Web editor: write narration across all chapters."),
    "narration-review":     ("mangaeasy.web.narration_review",                 "main",        "Manga: narration", "Web editor: review and QA narration."),
    "join-narration":       ("mangaeasy.narration.join",                       "main",        "Manga: narration", "Join per-chapter narration JSON files."),
    "normalize-narration":  ("mangaeasy.narration.normalize",                  "main",        "Manga: narration", "Normalize narration JSON text."),
    "clean-narration":      ("mangaeasy.narration.clean",                      "main",        "Manga: narration", "Clean narration JSON."),
    "backup-narration":     ("mangaeasy.narration.backup",                     "main",        "Manga: narration", "Back up narration JSON files."),
    "rename-file":          ("mangaeasy.narration.rename_file",                "main",        "Manga: narration", "Rename narration/media files by convention."),

    # ── Manga chapter workflow: render & export ───────────────────────────────
    "fade-audio":           ("mangaeasy.audio.fade",                           "main",        "Manga: render",    "Apply fades to chapter narration audio."),
    "render-video":         ("mangaeasy.video.render",                         "main",        "Manga: render",    "Render a chapter video from panels + audio."),
    "add-bgm":              ("mangaeasy.video.add_bg",                         "main",        "Manga: render",    "Add background music to a chapter video."),
    "join-chapters":        ("mangaeasy.video.join",                           "main",        "Manga: render",    "Rebuild chapters from panels + audio and add BGM."),
    "join-chapters-nobgm":  ("mangaeasy.video.join",                           "main_nobgm",  "Manga: render",    "Concatenate existing chapter videos (no BGM)."),
    "timestamps":           ("mangaeasy.video.timestamps",                     "main",        "Manga: render",    "Generate per-panel timestamps."),
    "to-pdf":               ("mangaeasy.images.pdf",                           "main",        "Manga: render",    "Export chapter images to a PDF."),
    "to-pdf-lossless":      ("mangaeasy.images.pdf_lossless",                  "main",        "Manga: render",    "Export images to a lossless PDF."),
    "convert-images":       ("mangaeasy.images.convert",                       "main",        "Manga: render",    "Convert / normalize image formats."),
    "watermark":            ("mangaeasy.images.watermark",                     "main",        "Manga: render",    "Apply a text watermark to images."),

    # ── Chapter bookkeeping ───────────────────────────────────────────────────
    "init-chapter":         ("mangaeasy.utils.init_chapter",                   "main",        "Manga: chapters",  "Create folders for a new chapter."),
    "increment-chapter":    ("mangaeasy.utils.increment",                      "main",        "Manga: chapters",  "Bump the chapter number in config.json."),
    "reset-chapter":        ("mangaeasy.utils.reset",                          "main",        "Manga: chapters",  "Reset chapter working state."),
    "fix-name":             ("mangaeasy.utils.fix_name",                       "main",        "Manga: chapters",  "Fix file naming for a chapter."),
    "clean-chapter":        ("mangaeasy.utils.clean_chapter",                  "main",        "Manga: chapters",  "Remove intermediate files for a chapter."),
}


def _group_order() -> list[str]:
    """Groups in first-seen order, so help output stays grouped and stable."""
    order: list[str] = []
    for _, _, group, _ in COMMANDS.values():
        if group not in order:
            order.append(group)
    return order


def _print_help(stream=sys.stdout) -> None:
    write = stream.write
    write(f"mangaeasy {__version__} - manga & image-to-video automation\n\n")
    write("Usage:\n")
    write("  mangaeasy <command> [args...]\n")
    write("  mangaeasy <command> --help     Show a command's own options\n")
    write("  mangaeasy --version\n\n")

    width = max(len(name) for name in COMMANDS) + 2
    for group in _group_order():
        write(f"{group}:\n")
        for name, (_, _, grp, help_text) in COMMANDS.items():
            if grp == group:
                write(f"  {name:<{width}}{help_text}\n")
        write("\n")


def _dispatch(command: str, rest: list[str]) -> int:
    module_path, func_name, _, _ = COMMANDS[command]
    module = importlib.import_module(module_path)
    func = getattr(module, func_name)
    # Present the subcommand's own argparse with a sensible prog name and let it
    # parse the remaining args exactly as if it were a standalone tool.
    sys.argv = [f"mangaeasy {command}", *rest]
    result = func()
    return result if isinstance(result, int) else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ("-h", "--help", "help"):
        # `mangaeasy help <command>` -> show that command's own help.
        if len(argv) >= 2 and argv[0] == "help" and argv[1] in COMMANDS:
            return _dispatch(argv[1], ["--help"])
        _print_help()
        return 0

    if argv[0] in ("-V", "--version", "version"):
        print(f"mangaeasy {__version__}")
        return 0

    command, rest = argv[0], argv[1:]
    if command not in COMMANDS:
        sys.stderr.write(f"mangaeasy: unknown command '{command}'\n")
        suggestions = difflib.get_close_matches(command, list(COMMANDS), n=3)
        if suggestions:
            sys.stderr.write("Did you mean: " + ", ".join(suggestions) + "?\n")
        sys.stderr.write("Run 'mangaeasy --help' to list all commands.\n")
        return 2

    return _dispatch(command, rest)


if __name__ == "__main__":
    raise SystemExit(main())
