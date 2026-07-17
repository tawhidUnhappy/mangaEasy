"""mediaconductor.command_spec — the single declarative source for command schemas.

One table describes every agent-facing command: its MCP tool name, its CLI
name, a description, JSON-schema properties, required arguments, and how each
property maps onto CLI flags. Both consumers read from here:

- `mediaconductor mcp` (mediaconductor/mcp_server.py) serves these as MCP tool schemas
  and builds argv from the flag mappings.
- `mediaconductor commands --json --full` (mediaconductor/cli.py) publishes the same
  schemas so a shell agent can discover a command's arguments without running
  sixty separate `--help` calls.

Adding a flag to a subcommand's argparse? Add it here too — this table is
what agents see. (Historically the MCP server kept its own private copy of
all of this, which could silently drift from the real argparse; now there is
exactly one copy and two renderers.)

Flag spec kinds: "value" (--flag VALUE), "json" (--flag JSON_OBJECT), "flag" (--flag when true),
"no-flag" (--no-flag when false), "list" (--flag V1 V2 ...),
"repeat" (--flag V1 --flag V2 ..., for argparse action="append" flags),
"positional" (bare value).
"""

from __future__ import annotations

_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_INT = {"type": "integer"}
_NUM = {"type": "number"}
_YOUTUBE_PROFILE = {
    "type": "string",
    "pattern": r"^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$",
    "default": "default",
    "description": "Isolated YouTube account profile (default: default; e.g. manga, song, ai-story).",
}
_YOUTUBE_AUTO_AUTH = {
    "type": "boolean",
    "default": True,
    "description": "Open Google browser consent automatically when the selected profile has "
                   "no usable token. Set false for headless/noninteractive operation.",
}
_ITEMS = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Item/chapter folder names or ranges, e.g. [\"01\", \"05-08\"]. "
                   "WARNING: omitting selects EVERY item in the project — for expensive "
                   "generation commands always pass the explicit batch.",
}
_PROJECT_ROOT = {
    "type": "string",
    "description": "Absolute path to the folder containing the item folders (usually library/<project>).",
}

# MCP tool name -> (cli command, description, {property: schema}, [required], {property: flag spec})
TOOLS: dict[str, tuple[str, str, dict, list[str], dict]] = {
    "modes": (
        "modes",
        "List the three isolated production modes, their dependencies, and the MCP restart command.",
        {"mode": {"type": "string", "enum": ["manga-video", "ai-story", "song-video"]}},
        [], {"mode": ("--mode", "value")},
    ),
    "setup": (
        "setup",
        "One-command provisioning: core binaries (ffmpeg/uv/git-lfs) + AI tool envs + model "
        "downloads, GPU-aware. VERY LONG-RUNNING on first run (tens of GB with a GPU) — prefer "
        "job_start. Safe to re-run — updates/resumes instead of reinstalling. Set dry_run=true "
        "to preview the plan.",
        {"all": {**_BOOL, "description": "Install every tool regardless of hardware."},
         "minimal": {**_BOOL, "description": "Core binaries only; no AI tool envs."},
         "mode": {"type": "string", "enum": ["manga-video", "ai-story", "song-video"],
                  "description": "Install only the selected mode's isolated toolchain."},
         "skip": {"type": "array", "items": {"type": "string"},
                  "description": "Tool names to skip, e.g. [\"z-image-turbo\"]."},
         "skip_models": _BOOL, "cpu": _BOOL, "cuda": _BOOL, "dry_run": _BOOL},
        [],
        {"all": ("--all", "flag"), "minimal": ("--minimal", "flag"),
         "mode": ("--mode", "value"), "skip": ("--skip", "repeat"),
         "skip_models": ("--skip-models", "flag"), "cpu": ("--cpu", "flag"),
         "cuda": ("--cuda", "flag"), "dry_run": ("--dry-run", "flag")},
    ),
    "download": (
        "download",
        "Download chapters from MangaDex — politely (API spacing, 429 backoff, jittered delays) "
        "and resumable. Pass the title URL directly; all=true grabs the whole series start to "
        "end in English (LONG-RUNNING — prefer job_start; already-complete chapters are skipped).",
        {"url": {**_STR, "description": "MangaDex title URL or manga UUID."},
         "name": {**_STR, "description": "Library folder name; derived from the title if omitted."},
         "chapters": {"type": "array", "items": {"type": "string"},
                      "description": "Specific chapters/ranges, e.g. [\"0-12\", \"14\"]."},
         "all": {**_BOOL, "description": "Every chapter available in the language."},
         "from_chapter": {**_NUM, "description": "With all: skip chapters below this number."},
         "to_chapter": {**_NUM, "description": "With all: skip chapters above this number."},
         "fresh": {**_BOOL, "description": "Bypass the local metadata cache."}},
        [],
        {"url": ("--url", "value"), "name": ("--name", "value"),
         "chapters": ("--chapters", "list"), "all": ("--all", "flag"),
         "from_chapter": ("--from", "value"), "to_chapter": ("--to", "value"),
         "fresh": ("--fresh", "flag")},
    ),
    "style_detect": (
        "style-detect",
        "Detect webtoon (vertical strips -> webtoon_split) vs paged manga (-> page_split) from "
        "the downloaded page dimensions. Returns a verdict plus sample image paths to confirm "
        "visually before cropping.",
        {"project_root": _PROJECT_ROOT, "items": _ITEMS,
         "source_subdir": {**_STR, "description": "Page-image folder inside each item (default: download)."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "source_subdir": ("--source-subdir", "value")},
    ),
    "webtoon_split": (
        "webtoon-split",
        "Crop webtoon items into panels (gutter detection + auto-split + gap rescue) and write "
        "verify sheets. The result lists per-item suspects and verify_images — inspect those "
        "images and clear every flag before narrating; fix misses via the overrides file.",
        {"project_root": _PROJECT_ROOT, "items": _ITEMS,
         "source_subdir": {**_STR, "description": "Page-image folder inside each item (default: download)."},
         "work_dir": {**_STR, "description": "Work dir for verify sheets (default: work)."},
         "overrides": {**_STR, "description": "JSON file with per-item split_at/merge fixes."},
         "force_style": {**_BOOL, "description": "Bypass the webtoon-vs-paged pre-flight guard "
                                                 "(only for deliberate mixed-format items)."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "source_subdir": ("--source-subdir", "value"),
         "work_dir": ("--work-dir", "value"), "overrides": ("--overrides", "value"),
         "force_style": ("--force-style", "flag")},
    ),
    "webtoon_cutcheck": (
        "webtoon-cutcheck",
        "Render full-resolution review windows around every forced auto-split cut and short "
        "panel from webtoon-split's ranges manifests, montaged into sheets. Read EVERY sheet "
        "and judge each flagged location on the art (FIX = cut through figure/speech bubble; "
        "ACCEPT = background/effect art, banners, bordered thin panels) before narrating.",
        {"project_root": _PROJECT_ROOT, "items": _ITEMS,
         "source_subdir": {**_STR, "description": "Page-image folder inside each item (default: download)."},
         "work_dir": {**_STR, "description": "Work dir holding webtoon_verify manifests (default: work)."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "source_subdir": ("--source-subdir", "value"),
         "work_dir": ("--work-dir", "value")},
    ),
    "webtoon_override": (
        "webtoon-override",
        "Add merge/split fixes to a webtoon-split overrides file with indices resolved from "
        "the ranges manifest — never compute merge indices by hand. merge_at_cut undoes a bad "
        "auto-split cut at stitched y; merge_panels fuses current panels 'A,B' (1-based sheet "
        "numbers); split_at forces a cut at y. Re-run webtoon-split with the file after.",
        {"file": {**_STR, "description": "Overrides JSON to create/extend."},
         "project_root": _PROJECT_ROOT,
         "item": {**_STR, "description": "Item the fixes apply to, e.g. '01'."},
         "merge_at_cut": {"type": "array", "items": {"type": "string"},
                          "description": "Stitched y values of bad cuts to undo."},
         "merge_panels": {"type": "array", "items": {"type": "string"},
                          "description": "Panel pairs to fuse, each 'A,B' (1-based)."},
         "split_at": {"type": "array", "items": {"type": "string"},
                      "description": "Stitched y values to force cuts at."},
         "show": {**_BOOL, "description": "Print the resolved overrides file."}},
        ["file", "project_root"],
        {"file": ("--file", "value"), "project_root": ("--project-root", "value"),
         "item": ("--item", "value"), "merge_at_cut": ("--merge-at-cut", "repeat"),
         "merge_panels": ("--merge-panels", "repeat"), "split_at": ("--split-at", "repeat"),
         "show": ("--show", "flag")},
    ),
    "panels_remap": (
        "panels-remap",
        "After re-running webtoon-split, map the archived old panels to the new crops and carry "
        "narration + audio over (texts verbatim, WAVs copied/concatenated) instead of "
        "re-narrating. Dry run by default; set apply=true once the report shows zero orphans. "
        "Review every shift/merge panel with narration-review-sheets afterwards.",
        {"project_root": _PROJECT_ROOT, "items": _ITEMS,
         "source_subdir": {**_STR, "description": "Page-image folder inside each item (default: download)."},
         "audio_root": {**_STR, "description": "Audio root (default: audio)."},
         "old_run": {**_STR, "description": "Archive run (e.g. run_0002) the narration was written against."},
         "apply": {**_BOOL, "description": "Write narration.json + audio (default: dry-run report)."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "source_subdir": ("--source-subdir", "value"),
         "audio_root": ("--audio-root", "value"), "old_run": ("--old-run", "value"),
         "apply": ("--apply", "flag")},
    ),
    "page_split": (
        "page-split",
        "Crop paged manga into panels with MAGI v3 detection (needs install-tool magi-v3; "
        "LONG-RUNNING — prefer job_start) and write verify overlays. Inspect the result's "
        "verify_images and clear every suspect before narrating.",
        {"project_root": _PROJECT_ROOT, "items": _ITEMS,
         "source_subdir": {**_STR, "description": "Page-image folder inside each item (default: download)."},
         "work_dir": {**_STR, "description": "Work dir for verify sheets (default: work)."},
         "overrides": {**_STR, "description": "JSON file with per-page box fixes."},
         "device": {"type": "string", "enum": ["auto", "cuda", "cpu"]},
         "force_style": {**_BOOL, "description": "Bypass the webtoon-vs-paged pre-flight guard "
                                                 "(only for deliberate mixed-format items)."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "source_subdir": ("--source-subdir", "value"),
         "work_dir": ("--work-dir", "value"), "overrides": ("--overrides", "value"),
         "device": ("--device", "value"), "force_style": ("--force-style", "flag")},
    ),
    "crop_qa": (
        "crop-qa",
        "Review crop verification artifacts with the local Gemma 4 model (needs install-tool "
        "gemma-4; LONG-RUNNING — prefer job_start). Renders review windows around every "
        "flagged cut/short panel (webtoon) or page overlay (paged), gets a fix/accept verdict "
        "per location, and prints the exact override command for every fix. Exit 3 = fixes "
        "proposed: apply them, re-split, re-run until clean.",
        {"project_root": _PROJECT_ROOT, "items": _ITEMS,
         "style": {"type": "string", "enum": ["auto", "webtoon", "paged"]},
         "work_dir": {**_STR, "description": "Work dir holding the verify artifacts (default: work)."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "style": ("--style", "value"), "work_dir": ("--work-dir", "value")},
    ),
    "characters": (
        "characters",
        "Create/validate <project-root>/characters.json — the cast registry (name, aliases, "
        "appearance, role) that grounds narration and speaker attribution. auto_draft samples "
        "panels and drafts it with the local Gemma 4 model (always draft:true — review the "
        "names before relying on them); init writes a hand-fill template.",
        {"project_root": _PROJECT_ROOT, "items": _ITEMS,
         "init": {**_BOOL, "description": "Write a template registry to fill in by hand."},
         "auto_draft": {**_BOOL, "description": "Draft the registry with Gemma 4 (needs install-tool gemma-4)."},
         "overwrite": {**_BOOL, "description": "Replace an existing characters.json."},
         "work_dir": {**_STR, "description": "Scratch root for the LLM run (default: work)."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "init": ("--init", "flag"), "auto_draft": ("--auto-draft", "flag"),
         "overwrite": ("--overwrite", "flag"), "work_dir": ("--work-dir", "value")},
    ),
    "narrate_auto": (
        "narrate-auto",
        "Draft grounded <item>/narration.json with the local Gemma 4 model (needs install-tool "
        "gemma-4; LONG-RUNNING — prefer job_start): panel images + OCR transcript + character "
        "registry, banner panels skipped, then narration-check + review sheets. ALWAYS exits 3 "
        "on success — the draft requires review against the sheets before TTS. Existing "
        "narration.json is never replaced without overwrite=true.",
        {"project_root": _PROJECT_ROOT, "items": _ITEMS,
         "work_dir": {**_STR, "description": "Scratch root (default: work)."},
         "chunk_size": {**_INT, "description": "Panels per vision request (default: 8)."},
         "overwrite": {**_BOOL, "description": "Replace existing narration.json files."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "work_dir": ("--work-dir", "value"), "chunk_size": ("--chunk-size", "value"),
         "overwrite": ("--overwrite", "flag")},
    ),
    "manga_auto": (
        "manga-auto",
        "One-command manga pipeline (VERY LONG-RUNNING — prefer job_start). stage=prep: "
        "download (with url) → style-detect → the correct splitter → crop-qa → "
        "panel-transcript → characters + narrate-auto, then exit 3 with a review checklist. "
        "stage=build (after review): TTS + render + join + normalize → video-validate → "
        "work-qa. Never publishes; exit 3 always means review the printed artifacts.",
        {"url": {**_STR, "description": "MangaDex title URL to download first."},
         "name": {**_STR, "description": "Library folder name (with url, or to locate an existing project)."},
         "project_root": {**_STR, "description": "Existing project folder (library/<name>); overrides name."},
         "items": _ITEMS,
         "stage": {"type": "string", "enum": ["prep", "build"], "default": "prep"},
         "tts": {"type": "string", "enum": ["auto", "kokoro", "indextts"]},
         "work_dir": {**_STR, "description": "Work dir (default: <workspace>/work)."},
         "audio_root": {**_STR, "description": "Audio root (default: <workspace>/audio)."},
         "output_root": {**_STR, "description": "Output root (default: <workspace>/output)."}},
        [],
        {"url": ("--url", "value"), "name": ("--name", "value"),
         "project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "stage": ("--stage", "value"), "tts": ("--tts", "value"),
         "work_dir": ("--work-dir", "value"), "audio_root": ("--audio-root", "value"),
         "output_root": ("--output-root", "value")},
    ),
    "narration_check": (
        "narration-check",
        "Validate narration.json/intro.json structure per item: files parse, every entry's image "
        "exists, every panel is covered, no empty narration. Run before generating audio. "
        "(Semantic accuracy and speaker attribution still need an agent reading the panels.)",
        {"project_root": _PROJECT_ROOT, "items": _ITEMS},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list")},
    ),
    "panel_transcript": (
        "panel-transcript",
        "OCR every panel into <item>/transcript.json with DeepSeek-OCR 2 (needs install-tool "
        "deepseek-ocr2; LONG-RUNNING — prefer job_start). Run BEFORE writing narration: the "
        "transcript grounds dialogue paraphrase and speaker attribution, and "
        "narration-review-sheets shows it next to each narration line during verification.",
        {"project_root": _PROJECT_ROOT, "items": _ITEMS,
         "force": {**_BOOL, "description": "Re-OCR panels that already have an ocr value."},
         "device": {"type": "string", "enum": ["auto", "cuda", "cpu"]},
         "seed_only": {**_BOOL, "description": "Write transcript skeletons without running OCR."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "force": ("--force", "flag"), "device": ("--device", "value"),
         "seed_only": ("--seed-only", "flag")},
    ),
    "narration_edit": (
        "narration-edit",
        "Upsert/delete/list narration.json (or intro.json) entries without hand-editing "
        "JSON. set_json takes a JSON array [{\"image\", \"narration\"}] inline; new images "
        "are inserted in name-sorted (reading) order; prune_audio deletes the WAVs of "
        "changed entries so the next audio run regenerates exactly those.",
        {"project_root": _PROJECT_ROOT,
         "item": {**_STR, "description": "Item folder, e.g. '01'."},
         "set_json": {**_STR, "description": "JSON array of entries to upsert."},
         "delete": {"type": "array", "items": {"type": "string"},
                    "description": "Image filenames whose entries to remove."},
         "intro": {**_BOOL, "description": "Edit intro.json instead of narration.json."},
         "prune_audio": {**_BOOL, "description": "Delete stale WAVs of changed entries."},
         "list": {**_BOOL, "description": "Print entries after any edits."}},
        ["project_root", "item"],
        {"project_root": ("--project-root", "value"), "item": ("--item", "value"),
         "set_json": ("--set-json", "value"), "delete": ("--delete", "repeat"),
         "intro": ("--intro", "flag"), "prune_audio": ("--prune-audio", "flag"),
         "list": ("--list", "flag")},
    ),
    "narration_review_sheets": (
        "narration-review-sheets",
        "Render sheets pairing every narration entry's panel image with the narration text and "
        "the panel's OCR transcript. Read EVERY sheet and verify per panel: the line describes "
        "THAT panel only, dialogue matches the OCR column, the speaker attribution is right, "
        "and the line reads naturally aloud. This is the semantic half narration-check skips.",
        {"project_root": _PROJECT_ROOT, "items": _ITEMS,
         "work_dir": {**_STR, "description": "Scratch root (default: work)."},
         "output_root": {**_STR, "description": "Review-sheet output root."},
         "per_sheet": {**_INT, "description": "Entries per review sheet (default: 4)."},
         "only_images": {"type": "array", "items": {"type": "string"},
                         "description": "Limit to these image names (e.g. panels-remap's review list)."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "work_dir": ("--work-dir", "value"), "output_root": ("--output-root", "value"),
         "per_sheet": ("--per-sheet", "value"),
         "only_images": ("--only-images", "list")},
    ),
    "thumbnail_compose": (
        "thumbnail-compose",
        "Compose a YouTube thumbnail: base art (e.g. best zimage variant) + bold stroked text "
        "blocks (rotate/shadow supported) + fat outlined block-arrows + white inset border at "
        "1280x720. Prefer spec_json (inline, no file). Tilt the big hook block a few degrees "
        "and keep arrows chunky so the markup reads hand-placed. Inspect the output image "
        "before uploading.",
        {"base": {**_STR, "description": "Absolute path to the base image."},
         "output": {**_STR, "description": "Absolute output PNG path."},
         "text": {"type": "array", "items": {"type": "string"},
                  "description": "Quick mode: 1-3 short text blocks (3-5 punchy words each)."},
         "spec_json": {**_STR, "description": "Full mode inline: JSON spec (blocks/arrows/border)."},
         "spec": {**_STR, "description": "Full mode: path to a JSON spec file."}},
        ["base", "output"],
        {"base": ("--base", "value"), "output": ("--output", "value"),
         "text": ("--text", "repeat"), "spec_json": ("--spec-json", "value"),
         "spec": ("--spec", "value")},
    ),
    "series_plan": (
        "series-plan",
        "Slice a project's items into fixed upload batches (12 per video by default), report "
        "per-batch readiness and published state, and name the next batch to produce.",
        {"project_root": _PROJECT_ROOT,
         "batch_size": {**_INT, "description": "Items per video (default 12)."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "batch_size": ("--batch-size", "value")},
    ),
    "series_mark_published": (
        "series-mark-published",
        "Record an uploaded batch in the project's publish.json (video id + timestamp) so "
        "series_plan advances to the next window. Call after a successful youtube_upload.",
        {"project_root": _PROJECT_ROOT,
         "items": {"type": "array", "items": {"type": "string"},
                   "description": "The batch's items, e.g. [\"01-12\"]."},
         "video_id": {**_STR, "description": "YouTube video id returned by youtube_upload."},
         "title": _STR,
         "url": {**_STR, "description": "Watch URL (derived from video_id when omitted)."},
         "profile": {**_YOUTUBE_PROFILE,
                     "description": "YouTube account profile used for the upload."},
         "channel_id": {**_STR, "description": "YouTube channel id that owns the upload."},
         "replaces_video_id": {**_STR,
                               "description": "Previous YouTube video id replaced by this upload."}},
        ["project_root", "items", "video_id"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "video_id": ("--video-id", "value"), "title": ("--title", "value"),
         "url": ("--url", "value"), "profile": ("--profile", "value"),
         "channel_id": ("--channel-id", "value"),
         "replaces_video_id": ("--replaces-video-id", "value")},
    ),
    "doctor": (
        "doctor",
        "Check this machine: ffmpeg/uv/git presence, GPU backend (cuda/mps/cpu), installed AI tools.",
        {"mode": {"type": "string", "enum": ["manga-video", "ai-story", "song-video"],
                  "description": "Limit readiness output to one production mode."},
         "check_updates": {**_BOOL, "description": "Also check installed AI tools for upstream updates."}},
        [],
        {"mode": ("--mode", "value"), "check_updates": ("--check-updates", "flag")},
    ),
    "smoke_test": (
        "smoke-test",
        "Prove the install works end to end: builds a tiny throwaway project, renders a real "
        "MP4 through the pipeline and verifies its streams/duration, then cleans up. Run after "
        "`setup` — doctor says the parts are installed, this proves they work together. "
        "tts='kokoro' additionally exercises the real TTS toolchain (model download on first use).",
        {"tts": {"type": "string", "enum": ["silent", "kokoro"]},
         "keep": {**_BOOL, "description": "Keep <work-dir>/smoke_test/ for inspection."}},
        [],
        {"tts": ("--tts", "value"), "keep": ("--keep", "flag")},
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
        "Generate per-panel narration audio with Kokoro TTS (CPU-friendly). LONG-RUNNING — "
        "prefer job_start. Existing audio is skipped unless overwrite=true (old takes are "
        "archived, never lost).",
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
         "project_name": _STR, "items": _ITEMS, "overwrite": _BOOL,
         "allow_gaps": {**_BOOL, "description": "Skip chapters genuinely missing from the source "
                        "instead of failing (never use it to paper over a failed render)."}},
        ["project_root", "output_root"],
        {"project_root": ("--project-root", "value"), "audio_root": ("--audio-root", "value"),
         "output_root": ("--output-root", "value"), "project_name": ("--project-name", "value"),
         "items": ("--items", "list"), "overwrite": ("--overwrite", "flag"),
         "allow_gaps": ("--allow-gaps", "flag")},
    ),
    "add_bgm": (
        "video-add-bgm",
        "Mix background music into the already-joined long video (cheap — no re-join). "
        "Writes a new timestamped file unless replace=true.",
        {"project_root": _PROJECT_ROOT, "output_root": _STR,
         "background_music": {**_STR, "description": "Absolute path to the music file."},
         "music_volume_db": {**_NUM, "description": "Music loudness in dB, negative = quieter (default -26)."},
         "project_name": _STR, "replace": _BOOL},
        ["project_root", "output_root", "background_music"],
        {"project_root": ("--project-root", "value"), "output_root": ("--output-root", "value"),
         "background_music": ("--background-music", "value"),
         "music_volume_db": ("--music-volume-db", "value"), "project_name": ("--project-name", "value"),
         "replace": ("--replace", "flag")},
    ),
    "run_full_pipeline": (
        "video",
        "The all-in-one pipeline: audio -> fade -> render -> optional join/BGM/final normalize. VERY "
        "LONG-RUNNING — prefer job_start. Prefer the single-step tools when iterating.",
        {"project_root": _PROJECT_ROOT, "audio_root": _STR, "output_root": _STR, "items": _ITEMS,
         "tts": {"type": "string", "enum": ["auto", "kokoro", "indextts"]},
         "speaker_wav": {**_STR, "description": "IndexTTS speaker reference WAV (defaults to "
                         "config.system.json -> tts.speaker_wav)."},
         "skip_audio": {**_BOOL, "description": "Reuse existing narration WAVs instead of "
                        "running TTS; the selected audio_source is still prepared and rendered."},
         "audio_source": {"type": "string", "enum": ["raw", "faded"], "default": "faded",
                          "description": "Narration source for rendering. 'faded' prepares a "
                                         "separate edge-faded derivative; 'raw' preserves the "
                                         "original waveform."},
         "audio_fade_ms": {**_NUM, "exclusiveMinimum": 0, "default": 8.0,
                           "description": "Fade duration at both edges of each narration WAV, "
                                          "in milliseconds, when audio_source='faded'."},
         "build_long_video": _BOOL,
         "allow_gaps": {**_BOOL, "description": "Skip chapters genuinely missing from the source "
                        "when joining instead of failing."},
         "normalize_audio": {**_BOOL, "description": "Two-pass loudness-normalize the complete "
                             "long-video mix for YouTube (-14 LUFS) after background music is added."},
         "overwrite_audio": {**_BOOL, "description": "Regenerate narration WAVs that already exist."},
         "overwrite_video": {**_BOOL, "description": "Re-render item videos that already exist — "
                             "REQUIRED after any panel/narration/audio change."},
         "emo_alpha": {**_NUM, "description": "IndexTTS only: strength of per-entry narration "
                       "'emotion' fields (default 0.6; 0 disables)."},
         "no_emotion": {**_BOOL, "description": "IndexTTS only: ignore narration 'emotion' fields."},
         "no_background_music": _BOOL,
         "background_music": _STR,
         "music_volume_db": _NUM},
        ["project_root", "audio_root", "output_root"],
        {"project_root": ("--project-root", "value"), "audio_root": ("--audio-root", "value"),
         "output_root": ("--output-root", "value"), "items": ("--items", "list"),
         "tts": ("--tts", "value"), "speaker_wav": ("--speaker-wav", "value"),
         "skip_audio": ("--skip-audio", "flag"),
         "audio_source": ("--audio-source", "value"),
         "audio_fade_ms": ("--audio-fade-ms", "value"),
         "build_long_video": ("--build-long-video", "flag"),
         "allow_gaps": ("--allow-gaps", "flag"),
         "normalize_audio": ("--normalize-audio", "flag"),
         "overwrite_audio": ("--overwrite-audio", "flag"),
         "overwrite_video": ("--overwrite-video", "flag"),
         "emo_alpha": ("--emo-alpha", "value"),
         "no_emotion": ("--no-emotion", "flag"),
         "no_background_music": ("--no-background-music", "flag"),
         "background_music": ("--background-music", "value"),
         "music_volume_db": ("--music-volume-db", "value")},
    ),
    "youtube_profiles": (
        "youtube-profiles",
        "List isolated YouTube account profiles, connection state, and cached channel. Use this "
        "before publishing so the intended account is selected explicitly.",
        {}, [], {},
    ),
    "youtube_status": (
        "youtube-status",
        "Status for one YouTube account profile. Set verify=true for a live token refresh and "
        "channel query; missing/invalid authorization opens browser consent automatically unless "
        "auto_auth=false. Offline status (verify=false) never opens a browser.",
        {"profile": _YOUTUBE_PROFILE,
         "auto_auth": _YOUTUBE_AUTO_AUTH,
         "verify": {**_BOOL, "description": "Also verify the token works right now (network call)."}},
        [],
        {"profile": ("--profile", "value"), "auto_auth": ("--no-auto-auth", "no-flag"),
         "verify": ("--verify", "flag")},
    ),
    "youtube_upload": (
        "youtube-upload",
        "Upload through the selected YouTube account profile (resumable, LONG-RUNNING — prefer "
        "job_start). Missing/invalid authorization opens browser consent unless auto_auth=false. "
        "Default privacy is private; one upload costs 1,600 quota units.",
        {"profile": _YOUTUBE_PROFILE,
         "auto_auth": _YOUTUBE_AUTO_AUTH,
         "video": {**_STR, "description": "Absolute path to the video file."},
         "title": {**_STR, "description": "Video title (max 100 chars)."},
         "description": _STR,
         "tags": {**_STR, "description": "Comma-separated tags, e.g. 'manga,recap'."},
         "privacy": {"type": "string", "enum": ["private", "unlisted", "public"]},
         "thumbnail": _STR, "made_for_kids": _BOOL, "contains_synthetic_media": _BOOL},
        ["video", "title"],
        {"profile": ("--profile", "value"), "auto_auth": ("--no-auto-auth", "no-flag"),
         "video": ("--video", "value"), "title": ("--title", "value"),
         "description": ("--description", "value"), "tags": ("--tags", "value"),
         "privacy": ("--privacy", "value"), "thumbnail": ("--thumbnail", "value"),
         "made_for_kids": ("--made-for-kids", "flag"),
         "contains_synthetic_media": ("--contains-synthetic-media", "flag")},
    ),
    "youtube_list": (
        "youtube-list",
        "List the selected profile's uploads (video id, title, privacy, published date) — the "
        "IDs youtube_delete/youtube_thumbnail need. ~2 quota units.",
        {"profile": _YOUTUBE_PROFILE,
         "auto_auth": _YOUTUBE_AUTO_AUTH,
         "limit": {**_INT, "description": "Maximum videos to return (default 25)."}},
        [],
        {"profile": ("--profile", "value"), "auto_auth": ("--no-auto-auth", "no-flag"),
         "limit": ("--limit", "value")},
    ),
    "youtube_delete": (
        "youtube-delete",
        "Look up and irreversibly delete one video through the selected profile. Requires "
        "confirm=true; omit it for a confirmation preview.",
        {"profile": _YOUTUBE_PROFILE,
         "auto_auth": _YOUTUBE_AUTO_AUTH,
         "video_id": {**_STR, "description": "YouTube video id."},
         "url": {**_STR, "description": "YouTube URL; alternative to video_id."},
         "confirm": {**_BOOL, "description": "Actually delete the video (irreversible)."}},
        [],
        {"profile": ("--profile", "value"), "auto_auth": ("--no-auto-auth", "no-flag"),
         "video_id": ("--video-id", "value"),
         "url": ("--url", "value"), "confirm": ("--confirm", "flag")},
    ),
    "youtube_thumbnail": (
        "youtube-thumbnail",
        "Set/replace a thumbnail through the selected profile without re-uploading. Requires a "
        "verified YouTube account for custom thumbnails.",
        {"profile": _YOUTUBE_PROFILE,
         "auto_auth": _YOUTUBE_AUTO_AUTH,
         "video_id": {**_STR, "description": "Video id, e.g. dQw4w9WgXcQ."},
         "image": {**_STR, "description": "Absolute path to the PNG/JPG (max 2 MB)."}},
        ["video_id", "image"],
        {"profile": ("--profile", "value"), "auto_auth": ("--no-auto-auth", "no-flag"),
         "video_id": ("--video-id", "value"),
         "image": ("--image", "value")},
    ),
    "bootstrap_tools": (
        "bootstrap-tools",
        "Download ffmpeg/uv/git-lfs (~100 MB, one-time) into this install's own tools dir. LONG-RUNNING.",
        {}, [], {},
    ),
    "install_tool": (
        "install-tool",
        "Install an external AI tool env (multi-GB download). LONG-RUNNING — prefer job_start.",
        {"name": {"type": "string", "enum": ["ace-step", "demucs", "whisperx", "kokoro-82m",
                                                       "faster-whisper", "index-tts", "magi-v3",
                                                       "deepseek-ocr2", "z-image-turbo"]},
         "ref": _STR, "skip_model": _BOOL, "cpu": _BOOL, "cuda": _BOOL, "update": _BOOL},
        ["name"],
        {"name": (None, "positional"), "ref": ("--ref", "value"),
         "skip_model": ("--skip-model", "flag"), "cpu": ("--cpu", "flag"),
         "cuda": ("--cuda", "flag"), "update": ("--update", "flag")},
    ),
    "deepseek_ocr2": (
        "deepseek-ocr2",
        "Run DeepSeek-OCR 2 over narration JSON files and write `ocr` fields. LONG-RUNNING — "
        "prefer job_start. Requires `mediaconductor install-tool deepseek-ocr2` first.",
        {"project_root": _PROJECT_ROOT, "items": _ITEMS,
         "force": {**_BOOL, "description": "Replace existing OCR fields."},
         "device": {"type": "string", "enum": ["auto", "cuda", "cpu"]}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "force": ("--force", "flag"), "device": ("--device", "value")},
    ),
    "work_status": (
        "work-status",
        "Multi-agent dashboard + resume command: per-item pipeline stage derived from the "
        "filesystem, active claims, recent shared notes; next=true returns only the unclaimed "
        "actionable tasks. Run first in every session.",
        {"project_root": _PROJECT_ROOT, "items": _ITEMS,
         "next": {**_BOOL, "description": "Only the unclaimed actionable tasks."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "next": ("--next", "flag")},
    ),
    "work_claim": (
        "work-claim",
        "Atomically claim an item+stage (or a shared resource like 'gpu') with a TTL lease so "
        "concurrent agents never duplicate work. Non-zero result text means it is held by "
        "another live agent — pick a different task.",
        {"project_root": _PROJECT_ROOT,
         "item": {**_STR, "description": "Item folder, e.g. 05 (omit for --resource)."},
         "stage": {"type": "string", "enum": ["download", "crop", "transcribe", "narrate",
                                              "audio", "render", "join", "thumbnail", "upload"]},
         "resource": {**_STR, "description": "Shared resource name, e.g. gpu."},
         "agent": _STR, "ttl_minutes": _INT,
         "release": {**_BOOL, "description": "Release instead of acquire."},
         "renew": {**_BOOL, "description": "Extend an existing own claim."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "item": ("--item", "value"),
         "stage": ("--stage", "value"), "resource": ("--resource", "value"),
         "agent": ("--agent", "value"), "ttl_minutes": ("--ttl-minutes", "value"),
         "release": ("--release", "flag"), "renew": ("--renew", "flag")},
    ),
    "work_note": (
        "work-note",
        "Shared append-only project notebook for agent handoff (character names, speaker "
        "conventions, tone decisions, warnings). Omit 'add' to read.",
        {"project_root": _PROJECT_ROOT,
         "add": {**_STR, "description": "Note text to append."},
         "topic": {**_STR, "description": "characters / speakers / tone / decisions / warnings."},
         "agent": _STR, "limit": _INT},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "add": ("--add", "value"),
         "topic": ("--topic", "value"), "agent": ("--agent", "value"),
         "limit": ("--limit", "value")},
    ),
    "work_qa": (
        "work-qa",
        "Aggregated machine-checkable QA gate over generated crops/narration/audio/renders. "
        "Each problem includes the exact fix command — loop qa->fix->qa until ok=true. "
        "review-severity items point at sheets that need a vision pass.",
        {"project_root": _PROJECT_ROOT, "items": _ITEMS,
         "errors_only": {**_BOOL, "description": "Hide review/info items."},
         "max_problems": {**_INT, "description": "Cap list for small context windows (default 25)."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "errors_only": ("--errors-only", "flag"), "max_problems": ("--max-problems", "value")},
    ),
    "work_artifacts": (
        "work-artifacts",
        "Inventory of reusable generated artifacts (item renders, long-video takes, audio takes, "
        "transcripts, QA sheets, cached music beds) with a reuse hint each — check before "
        "regenerating anything expensive.",
        {"project_root": _PROJECT_ROOT},
        ["project_root"],
        {"project_root": ("--project-root", "value")},
    ),
    "generate_image": (
        "zimage",
        "Generate images with Z-Image Turbo (text-to-image). LONG-RUNNING on first call "
        "(model load ~1-2 min; then ~10-30 s per image on a GPU) — prefer job_start. Requires "
        "`mediaconductor install-tool z-image-turbo` first. Long descriptive prompts work best.",
        {"prompt": {**_STR, "description": "Text prompt (English or Chinese)."},
         "output": {**_STR, "description": "Absolute output PNG path."},
         "width": _INT, "height": _INT,
         "count": {**_INT, "description": "Number of variants (files get _01.._NN suffixes)."},
         "seed": _INT,
         "strategy": {"type": "string", "enum": ["auto", "bf16", "nf4", "offload", "cpu"],
                      "description": "VRAM strategy; auto detects GPU/VRAM and is the default."}},
        ["prompt", "output"],
        {"prompt": ("--prompt", "value"), "output": ("--output", "value"),
         "width": ("--width", "value"), "height": ("--height", "value"),
         "count": ("--count", "value"), "seed": ("--seed", "value"),
         "strategy": ("--strategy", "value")},
    ),
    "story_init": (
        "story-init",
        "Create an AI Story manifest. The agent must then fill its continuity bible and scenes before building.",
        {"project_root": _PROJECT_ROOT, "title": _STR,
         "story": {**_STR, "description": "Complete source story text; use exactly one of story/story_file."},
         "story_file": {**_STR, "description": "UTF-8 source story file; use exactly one of story/story_file."},
         "force": _BOOL},
        ["project_root", "title"],
        {"project_root": ("--project-root", "value"), "title": ("--title", "value"),
         "story": ("--story", "value"), "story_file": ("--story-file", "value"),
         "force": ("--force", "flag")},
    ),
    "generate_song": (
        "ace-step",
        "LONG-RUNNING. Generate a WAV from a music prompt and canonical lyrics with pinned ACE-Step 1.5.",
        {"prompt": _STR, "lyrics_file": _STR, "output": _STR, "seed": _INT,
         "duration": _NUM, "language": _STR, "bpm": _INT,
         "device": {"type": "string", "enum": ["auto", "cuda", "mps", "cpu"]}},
        ["prompt", "lyrics_file", "output"],
        {"prompt": ("--prompt", "value"), "lyrics_file": ("--lyrics-file", "value"),
         "output": ("--output", "value"), "seed": ("--seed", "value"),
         "duration": ("--duration", "value"), "language": ("--language", "value"),
         "bpm": ("--bpm", "value"), "device": ("--device", "value")},
    ),
    "separate_vocals": (
        "demucs",
        "LONG-RUNNING. Separate deterministic vocals.wav and accompaniment.wav with the pinned local HTDemucs-ft snapshot. Runtime network access is disabled.",
        {"audio": _STR, "output_dir": _STR,
         "device": {"type": "string", "enum": ["auto", "cuda", "cpu"]}},
        ["audio", "output_dir"],
        {"audio": ("--audio", "value"), "output_dir": ("--output-dir", "value"),
         "device": ("--device", "value")},
    ),
    "align_lyrics": (
        "whisperx",
        "LONG-RUNNING. Time supplied canonical lyrics from a vocal stem and write styled SRT/ASS; output includes confidence and a review gate.",
        {"audio": _STR, "lyrics_file": _STR, "output_dir": _STR,
         "language": _STR, "device": {"type": "string", "enum": ["auto", "cuda", "cpu"]},
         "width": _INT, "height": _INT,
         "minimum_confidence": {"type": "number", "minimum": 0, "maximum": 1,
                                "default": 0.72},
         "font_name": _STR, "font_size_ratio": _NUM, "outline": _NUM, "shadow": _NUM,
         "fade_in_ms": _INT, "fade_out_ms": _INT,
         "alignment": {"type": "integer", "minimum": 1, "maximum": 9},
         "margin_vertical_ratio": _NUM},
        ["audio", "lyrics_file", "output_dir"],
        {"audio": ("--audio", "value"), "lyrics_file": ("--lyrics-file", "value"),
         "output_dir": ("--output-dir", "value"), "language": ("--language", "value"),
         "device": ("--device", "value"), "width": ("--width", "value"),
         "height": ("--height", "value"), "font_name": ("--font-name", "value"),
         "minimum_confidence": ("--minimum-confidence", "value"),
         "font_size_ratio": ("--font-size-ratio", "value"),
         "outline": ("--outline", "value"), "shadow": ("--shadow", "value"),
         "fade_in_ms": ("--fade-in-ms", "value"),
         "fade_out_ms": ("--fade-out-ms", "value"),
         "alignment": ("--alignment", "value"),
         "margin_vertical_ratio": ("--margin-vertical-ratio", "value")},
    ),
    "story_check": (
        "story-check",
        "Validate continuity anchors, scene references, image prompts, narration, and publish metadata.",
        {"manifest": {**_STR, "description": "Absolute story.json path; use exactly one of manifest/project_root."},
         "project_root": {**_PROJECT_ROOT, "description": "Folder containing story.json; use exactly one of manifest/project_root."},
         "for_publish": _BOOL},
        [], {"manifest": ("--manifest", "value"), "project_root": ("--project-root", "value"),
             "for_publish": ("--for-publish", "flag")},
    ),
    "story_build": (
        "story-build",
        "LONG-RUNNING. Build a validated story in deterministic stages. Publishing is never implicit; pass stage=publish explicitly.",
        {"manifest": {**_STR, "description": "Absolute story.json path; use exactly one of manifest/project_root."},
         "project_root": {**_PROJECT_ROOT, "description": "Folder containing story.json; use exactly one of manifest/project_root."},
         "stage": {"type": "string", "enum": ["prepare", "images", "video", "publish", "all"]},
         "overwrite": _BOOL, "dry_run": _BOOL,
         "speaker_wav": _STR,
         "privacy": {"type": "string", "enum": ["private", "unlisted", "public"]}},
        [],
        {"manifest": ("--manifest", "value"), "project_root": ("--project-root", "value"),
         "stage": ("--stage", "value"),
         "overwrite": ("--overwrite", "flag"), "dry_run": ("--dry-run", "flag"),
         "speaker_wav": ("--speaker-wav", "value"), "privacy": ("--privacy", "value")},
    ),
    "song_init": (
        "song-init",
        "Create a Song Video manifest using supplied canonical lyrics and the editable minimalistic-sky visual default.",
        {"project_root": _PROJECT_ROOT, "title": _STR,
         "lyrics": {**_STR, "description": "Canonical lyrics; use exactly one of lyrics/lyrics_file."},
         "lyrics_file": {**_STR, "description": "UTF-8 canonical lyrics file; use exactly one of lyrics/lyrics_file."},
         "music_prompt": _STR, "audio": _STR, "force": _BOOL},
        ["project_root", "title"],
        {"project_root": ("--project-root", "value"), "title": ("--title", "value"),
         "lyrics": ("--lyrics", "value"), "lyrics_file": ("--lyrics-file", "value"),
         "music_prompt": ("--music-prompt", "value"),
         "audio": ("--audio", "value"), "force": ("--force", "flag")},
    ),
    "song_check": (
        "song-check",
        "Validate canonical lyrics, generation/audio source, alignment, visual, rights, and publish metadata.",
        {"manifest": {**_STR, "description": "Absolute song.json path; use exactly one of manifest/project_root."},
         "project_root": {**_PROJECT_ROOT, "description": "Folder containing song.json; use exactly one of manifest/project_root."},
         "for_publish": {**_BOOL, "description": "Turn missing rights confirmations into errors."}},
        [],
        {"manifest": ("--manifest", "value"), "project_root": ("--project-root", "value"),
         "for_publish": ("--for-publish", "flag")},
    ),
    "song_build": (
        "song-build",
        "LONG-RUNNING. Generate/ingest song audio, isolate vocals, time canonical lyrics, render, and explicitly publish.",
        {"manifest": {**_STR, "description": "Absolute song.json path; use exactly one of manifest/project_root."},
         "project_root": {**_PROJECT_ROOT, "description": "Folder containing song.json; use exactly one of manifest/project_root."},
         "stage": {"type": "string", "enum": ["prepare", "generate", "separate", "align", "visual", "render", "publish", "all"]},
         "overwrite": _BOOL, "dry_run": _BOOL,
         "privacy": {"type": "string", "enum": ["private", "unlisted", "public"]}},
        [],
        {"manifest": ("--manifest", "value"), "project_root": ("--project-root", "value"),
         "stage": ("--stage", "value"),
         "overwrite": ("--overwrite", "flag"), "dry_run": ("--dry-run", "flag"),
         "privacy": ("--privacy", "value")},
    ),
    "job_start": (
        "job-start",
        "Start one mode-visible MCP tool as a DETACHED background job and return a job id. "
        "Arguments are validated against that tool's typed schema; raw CLI argv is deliberately "
        "not accepted. Poll job_status for progress/result.",
        {"tool": {**_STR, "description": "A mode-visible MCP tool name, e.g. 'run_full_pipeline'."},
          "arguments": {"type": "object",
                        "description": "The target tool's normal typed arguments object."}},
        ["tool"],
        {"tool": ("--tool", "value"), "arguments": ("--arguments-json", "json")},
    ),
    "job_status": (
        "job-status",
        "Status of one background job: running/succeeded/failed, exit code, progress markers, "
        "parsed MEDIACONDUCTOR_RESULT payload, and the tail of its log.",
        {"job_id": {**_STR, "description": "Id returned by job_start."},
         "tail": {**_INT, "description": "Log tail lines to include (default 20)."}},
        ["job_id"],
        {"job_id": (None, "positional"), "tail": ("--tail", "value")},
    ),
    "job_list": (
        "jobs",
        "List background jobs (running and finished) with their ids, commands, and states.",
        {}, [], {},
    ),
}

# Commands whose --json flag should be appended automatically by the MCP server.
JSON_COMMANDS = {"modes", "doctor", "where", "library-list", "video-check", "video-validate",
                 "video-audio-audit", "youtube-profiles", "youtube-status", "youtube-upload",
                 "style-detect", "narration-check", "series-plan", "crop-qa", "characters",
                 "work-status", "work-claim", "work-note", "work-qa", "work-artifacts",
                 "youtube-list", "youtube-delete", "youtube-thumbnail",
                 "story-init", "story-check", "song-init", "song-check",
                 "job-status", "jobs"}

# CLI commands that run for minutes to hours. Surfaced structurally so agents
# can decide to background them (job-start / harness background shells)
# without parsing prose.
LONG_RUNNING = {"setup", "download", "webtoon-split", "page-split", "panel-transcript",
                "video", "video-audio", "video-audio-indextts", "video-render",
                "video-join", "video-normalize-audio", "zimage", "deepseek-ocr2",
                "install-tool", "bootstrap-tools", "youtube-upload", "smoke-test",
                "story-build", "song-build", "ace-step", "demucs", "whisperx",
                "llm", "crop-qa", "narrate-auto", "manga-auto"}

# cli command name -> mcp tool name (reverse index)
CLI_TO_TOOL: dict[str, str] = {cli: tool for tool, (cli, *_rest) in TOOLS.items()}


def cli_args_schema(cli_name: str, mode: str | None = None) -> dict | None:
    """The argument schema for one CLI command, flags included, or None.

    Shape (consumed by `mediaconductor commands --json --full`):
    {"<prop>": {"type": ..., "description": ..., "flag": "--project-root",
                "kind": "value", "required": true}, ...}
    """
    tool = CLI_TO_TOOL.get(cli_name)
    if tool is None:
        return None
    _cli, _desc, props, required, flags = TOOLS[tool]
    schema: dict = {}
    for prop, prop_schema in props.items():
        flag, kind = flags.get(prop, (None, "value"))
        schema[prop] = {
            **prop_schema,
            "flag": flag,
            "kind": kind,
            "required": prop in required,
        }
    if cli_name in JSON_COMMANDS:
        schema["json"] = {
            "type": "boolean", "flag": "--json", "kind": "flag",
            "required": False,
            "description": "Emit the machine-readable JSON report.",
        }
    if mode and cli_name in {"setup", "doctor"} and "mode" in schema:
        schema["mode"]["enum"] = [mode]
        schema["mode"]["default"] = mode
    if mode and cli_name == "install-tool" and "name" in schema:
        from mediaconductor.tools.setup import MODE_TOOLS
        schema["name"]["enum"] = list(MODE_TOOLS[mode])
    return schema
