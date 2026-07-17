"""Mode registry shared by the CLI, MCP server, setup, and agent skill.

The registry is intentionally small and declarative.  An MCP client connected
for one mode should never have to ingest the schemas for the other pipelines.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from mediaconductor.brand import CLI_NAME


@dataclass(frozen=True)
class ModeSpec:
    key: str
    title: str
    description: str
    commands: frozenset[str]
    tools: frozenset[str]
    required_external_tools: tuple[str, ...]
    optional_external_tools: tuple[str, ...] = ()


COMMON_COMMANDS = frozenset({
    "commands", "modes", "where", "doctor", "setup", "smoke-test",
    "install-tool", "bootstrap-tools", "tools", "mcp", "job-start",
    "job-status", "jobs", "youtube-profiles", "youtube-auth",
    "youtube-status", "youtube-logout",
})

COMMON_TOOLS = frozenset({
    "modes", "setup", "doctor", "where", "install_tool", "youtube_profiles",
    "youtube_status", "job_start", "job_status", "job_list",
})

PUBLISH_COMMANDS = frozenset({
    "youtube-upload", "youtube-list", "youtube-delete", "youtube-thumbnail",
})

PUBLISH_TOOLS = frozenset({
    "youtube_upload", "youtube_list", "youtube_delete", "youtube_thumbnail",
})

MANGA_COMMANDS = frozenset({
    "library-list", "series-plan", "series-mark-published", "work-status",
    "work-claim", "work-note", "work-qa", "work-artifacts", "video",
    "video-audio", "video-audio-indextts", "video-render", "video-join",
    "video-add-bgm", "video-check", "narration-check",
    "narration-review-sheets", "narration-edit", "video-validate",
    "video-audio-audit", "video-fade-audio", "video-normalize-audio",
    "video-clean-audio", "video-clean-video", "video-clean-work",
    "video-clean-all", "audio-takes-list", "audio-takes-restore",
    "index-tts", "deepseek-ocr2", "zimage", "download", "style-detect",
    "gutter-split", "webtoon-split", "webtoon-cutcheck",
    "webtoon-override", "panels-remap", "page-split", "panel-transcript",
    "llm", "crop-qa", "characters", "narrate-auto", "manga-auto",
    "to-pdf", "to-pdf-lossless", "convert-images", "thumbnail-compose",
    "watermark", "ai-zip",
})

MANGA_TOOLS = frozenset({
    "library_list", "download", "style_detect", "webtoon_split",
    "page_split", "panel_transcript", "series_plan",
    "series_mark_published", "video_check", "narration_check",
    "generate_audio", "render_videos", "build_long_video", "add_bgm",
    "run_full_pipeline",
    "video_validate", "work_status", "work_claim", "work_note", "work_qa",
    "work_artifacts", "generate_image",
    "crop_qa", "characters", "narrate_auto", "manga_auto",
})

STORY_COMMANDS = frozenset({"story-init", "story-check", "story-build"})

STORY_TOOLS = frozenset({"story_init", "story_check", "story_build"})

SONG_COMMANDS = frozenset({"song-init", "song-check", "song-build"})

SONG_TOOLS = frozenset({"song_init", "song_check", "song_build"})


MODES: dict[str, ModeSpec] = {
    "manga-video": ModeSpec(
        key="manga-video",
        title="Manga Video",
        description="Acquire manga, crop and verify panels, narrate, render, and publish recap videos.",
        commands=COMMON_COMMANDS | PUBLISH_COMMANDS | MANGA_COMMANDS,
        tools=COMMON_TOOLS | PUBLISH_TOOLS | MANGA_TOOLS,
        required_external_tools=("kokoro-82m",),
        optional_external_tools=("index-tts", "magi-v3", "deepseek-ocr2", "z-image-turbo",
                                 "gemma-4"),
    ),
    "ai-story": ModeSpec(
        key="ai-story",
        title="AI Story",
        description="Turn a written story and continuity bible into continuity-checked scene art, narration, video, and upload.",
        commands=COMMON_COMMANDS | PUBLISH_COMMANDS | STORY_COMMANDS,
        tools=COMMON_TOOLS | PUBLISH_TOOLS | STORY_TOOLS,
        required_external_tools=("kokoro-82m", "z-image-turbo"),
        optional_external_tools=("index-tts",),
    ),
    "song-video": ModeSpec(
        key="song-video",
        title="Song & Lyrics Video",
        description="Generate a song, separate vocals, align verified lyrics, render a lyric video, and publish it.",
        commands=COMMON_COMMANDS | PUBLISH_COMMANDS | SONG_COMMANDS,
        tools=COMMON_TOOLS | PUBLISH_TOOLS | SONG_TOOLS,
        required_external_tools=("ace-step", "demucs", "whisperx", "z-image-turbo"),
    ),
}


def normalize_mode(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {"manga": "manga-video", "story": "ai-story", "song": "song-video"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in MODES:
        raise ValueError(f"unknown mode '{value}'. Choose: {', '.join(MODES)}")
    return normalized


def mode_payload(spec: ModeSpec) -> dict:
    data = asdict(spec)
    data["commands"] = sorted(spec.commands)
    data["tools"] = sorted(spec.tools)
    data["skill_path"] = str(resolve_skill_path(spec.key))
    return data


def resolve_skill_path(mode: str) -> Path:
    """Return the selected skill from a checkout, wheel, or frozen bundle."""
    package_dir = Path(__file__).resolve().parent
    candidates = (
        package_dir / "agent_skills" / mode,
        package_dir.parent / "skills" / mode,
    )
    for candidate in candidates:
        if (candidate / "SKILL.md").is_file():
            return candidate
    # Preserve a deterministic path even in a damaged distribution so its
    # missing bundled skill is directly diagnosable.
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} modes",
        description="List the isolated production modes and their dependencies.",
    )
    parser.add_argument("--mode", choices=tuple(MODES), help="Show one mode only.")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    selected = [MODES[args.mode]] if args.mode else list(MODES.values())
    payload = {"modes": [mode_payload(spec) for spec in selected]}
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    for spec in selected:
        print(f"{spec.title} ({spec.key})")
        print(f"  {spec.description}")
        print(f"  Setup: {CLI_NAME} setup --mode {spec.key}")
        print(f"  MCP:   {CLI_NAME} mcp --mode {spec.key} --allow-root <workspace>")
        print(f"  Skill: {resolve_skill_path(spec.key)}")
        print(f"  Tools: {', '.join(spec.required_external_tools)}")
        if spec.optional_external_tools:
            print(f"  Optional: {', '.join(spec.optional_external_tools)}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
