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
# "no-flag" (--no-flag when false), "list" (--flag V1 V2 ...),
# "repeat" (--flag V1 --flag V2 ..., for argparse action="append" flags).
TOOLS: dict[str, tuple[str, str, dict, list[str], dict]] = {
    "setup": (
        "setup",
        "One-command provisioning: core binaries (ffmpeg/uv/git-lfs) + AI tool envs + model "
        "downloads, GPU-aware. VERY LONG-RUNNING on first run (tens of GB with a GPU). Safe to "
        "re-run — updates/resumes instead of reinstalling. Set dry_run=true to preview the plan.",
        {"all": {**_BOOL, "description": "Install every tool regardless of hardware."},
         "minimal": {**_BOOL, "description": "Core binaries only; no AI tool envs."},
         "skip": {"type": "array", "items": {"type": "string"},
                  "description": "Tool names to skip, e.g. [\"z-image-turbo\"]."},
         "dry_run": _BOOL},
        [],
        {"all": ("--all", "flag"), "minimal": ("--minimal", "flag"),
         "skip": ("--skip", "repeat"), "dry_run": ("--dry-run", "flag")},
    ),
    "download": (
        "download",
        "Download chapters from MangaDex — politely (API spacing, 429 backoff, jittered delays) "
        "and resumable. Pass the title URL directly; all=true grabs the whole series start to "
        "end in English (LONG-RUNNING; already-complete chapters are skipped).",
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
        {"project_root": _PROJECT_ROOT, "items": _ITEMS},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list")},
    ),
    "webtoon_split": (
        "webtoon-split",
        "Crop webtoon items into panels (gutter detection + auto-split + gap rescue) and write "
        "verify sheets. The result lists per-item suspects and verify_images — inspect those "
        "images and clear every flag before narrating; fix misses via the overrides file.",
        {"project_root": _PROJECT_ROOT, "items": _ITEMS,
         "work_dir": {**_STR, "description": "Work dir for verify sheets (default: work)."},
         "overrides": {**_STR, "description": "JSON file with per-item split_at/merge fixes."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "work_dir": ("--work-dir", "value"), "overrides": ("--overrides", "value")},
    ),
    "webtoon_cutcheck": (
        "webtoon-cutcheck",
        "Render full-resolution review windows around every forced auto-split cut and short "
        "panel from webtoon-split's ranges manifests, montaged into sheets. Read EVERY sheet "
        "and judge each flagged location on the art (FIX = cut through figure/speech bubble; "
        "ACCEPT = background/effect art, banners, bordered thin panels) before narrating.",
        {"project_root": _PROJECT_ROOT, "items": _ITEMS,
         "work_dir": {**_STR, "description": "Work dir holding webtoon_verify manifests (default: work)."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
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
         "audio_root": {**_STR, "description": "Audio root (default: audio)."},
         "old_run": {**_STR, "description": "Archive run (e.g. run_0002) the narration was written against."},
         "apply": {**_BOOL, "description": "Write narration.json + audio (default: dry-run report)."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "audio_root": ("--audio-root", "value"), "old_run": ("--old-run", "value"),
         "apply": ("--apply", "flag")},
    ),
    "page_split": (
        "page-split",
        "Crop paged manga into panels with MAGI v3 detection (needs install-tool magi-v3; "
        "LONG-RUNNING) and write verify overlays. Inspect the result's verify_images and clear "
        "every suspect before narrating.",
        {"project_root": _PROJECT_ROOT, "items": _ITEMS,
         "work_dir": {**_STR, "description": "Work dir for verify sheets (default: work)."},
         "overrides": {**_STR, "description": "JSON file with per-page box fixes."},
         "device": {"type": "string", "enum": ["auto", "cuda", "cpu"]}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "work_dir": ("--work-dir", "value"), "overrides": ("--overrides", "value"),
         "device": ("--device", "value")},
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
        "deepseek-ocr2; LONG-RUNNING). Run BEFORE writing narration: the transcript grounds "
        "dialogue paraphrase and speaker attribution, and narration-review-sheets shows it "
        "next to each narration line during verification.",
        {"project_root": _PROJECT_ROOT, "items": _ITEMS,
         "force": {**_BOOL, "description": "Re-OCR panels that already have an ocr value."},
         "device": {"type": "string", "enum": ["auto", "cuda", "cpu"]}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "force": ("--force", "flag"), "device": ("--device", "value")},
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
         "only_images": {"type": "array", "items": {"type": "string"},
                         "description": "Limit to these image names (e.g. panels-remap's review list)."}},
        ["project_root"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
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
         "title": _STR},
        ["project_root", "items", "video_id"],
        {"project_root": ("--project-root", "value"), "items": ("--items", "list"),
         "video_id": ("--video-id", "value"), "title": ("--title", "value")},
    ),
    "doctor": (
        "doctor",
        "Check this machine: ffmpeg/uv/git presence, GPU backend (cuda/mps/cpu), installed AI tools.",
        {"check_updates": {**_BOOL, "description": "Also check installed AI tools for upstream updates."}},
        [],
        {"check_updates": ("--check-updates", "flag")},
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
         "music_volume_db": {**_NUM, "description": "Music loudness in dB, negative = quieter (default -22)."},
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
         "no_background_music": _BOOL,
         "background_music": _STR,
         "music_volume_db": _NUM},
        ["project_root", "audio_root", "output_root"],
        {"project_root": ("--project-root", "value"), "audio_root": ("--audio-root", "value"),
         "output_root": ("--output-root", "value"), "items": ("--items", "list"),
         "tts": ("--tts", "value"), "build_long_video": ("--build-long-video", "flag"),
         "no_background_music": ("--no-background-music", "flag"),
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
    "youtube_list": (
        "youtube-list",
        "List the connected channel's uploads (video id, title, privacy, published date) — "
        "the IDs youtube_delete/youtube_thumbnail need. ~2 quota units.",
        {"limit": {**_INT, "description": "Maximum videos to return (default 25)."}},
        [],
        {"limit": ("--limit", "value")},
    ),
    "youtube_thumbnail": (
        "youtube-thumbnail",
        "Set/replace the thumbnail of an already-uploaded video — iterate on thumbnail art or "
        "markup without re-uploading. Needs the same auth as youtube-upload and a verified "
        "YouTube account for custom thumbnails.",
        {"video_id": {**_STR, "description": "Video id, e.g. dQw4w9WgXcQ."},
         "image": {**_STR, "description": "Absolute path to the PNG/JPG (max 2 MB)."}},
        ["video_id", "image"],
        {"video_id": ("--video-id", "value"), "image": ("--image", "value")},
    ),
    "bootstrap_tools": (
        "bootstrap-tools",
        "Download ffmpeg/uv/git-lfs (~100 MB, one-time) into this install's own tools dir. LONG-RUNNING.",
        {}, [], {},
    ),
    "install_tool": (
        "install-tool",
        "Install an external AI tool env (multi-GB download). LONG-RUNNING.",
        {"name": {"type": "string", "enum": ["kokoro-82m", "index-tts", "magi-v3", "deepseek-ocr2", "z-image-turbo"]},
         "update": _BOOL},
        ["name"],
        {"name": (None, "positional"), "update": ("--update", "flag")},
    ),
    "deepseek_ocr2": (
        "deepseek-ocr2",
        "Run DeepSeek-OCR 2 over narration JSON files and write `ocr` fields. LONG-RUNNING. "
        "Requires `mangaeasy install-tool deepseek-ocr2` first.",
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
        "(model load ~1-2 min; then ~10-30 s per image on a GPU). Requires "
        "`mangaeasy install-tool z-image-turbo` first. Long descriptive prompts work best.",
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
}

# Commands whose --json flag should be appended automatically.
_JSON_COMMANDS = {"doctor", "where", "library-list", "video-check", "video-validate",
                  "video-audio-audit", "youtube-status", "youtube-upload",
                  "style-detect", "narration-check", "series-plan",
                  "work-status", "work-claim", "work-note", "work-qa", "work-artifacts",
                  "youtube-list"}


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
        elif kind == "repeat":
            for v in value or []:
                args.extend([flag, str(v)])
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
