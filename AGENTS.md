# MediaConductor agent entry point

MediaConductor (formerly mangaEasy) has three isolated production modes. Load exactly one skill so unrelated pipelines do not consume context:

| Request | Read only this skill |
|---|---|
| Manga/manhwa/webtoon recap or image-panel narration | [`skills/manga-video/SKILL.md`](skills/manga-video/SKILL.md) |
| Written story to consistent scene art + narration video | [`skills/ai-story/SKILL.md`](skills/ai-story/SKILL.md) |
| Song generation or correctly timed lyrics video | [`skills/song-video/SKILL.md`](skills/song-video/SKILL.md) |
| Mode is unclear or setup-only | [`skills/media-conductor/SKILL.md`](skills/media-conductor/SKILL.md) |

From a fresh clone:

```bash
uv sync
uv run mediaconductor modes --json
uv run mediaconductor setup --mode <manga-video|ai-story|song-video>
uv run mediaconductor doctor --mode <mode> --json
```

For MCP, register `mediaconductor mcp --mode <mode> --allow-root <workspace>`. Repeat `--allow-root` only for additional intentional workspaces; when omitted it defaults to the server's startup directory. The no-mode server is a small router; `--all-tools` is only a legacy/debug escape hatch. Long operations must use the typed `job_start` MCP tool or the built-in background job commands.

Publishing is always explicit. Story and song builds stop at local QA gates; do not bypass rights, voice-consent, visual/timing review, or synthetic-media disclosure fields. Never expose OAuth token files or install heavy models into the core environment.

When changing the software itself, read [CLAUDE.md](CLAUDE.md), preserve existing user changes, and run `uv run ruff check .` plus `uv run pytest`.
