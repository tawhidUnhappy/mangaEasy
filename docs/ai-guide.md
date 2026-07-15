# AI agent guide

MediaConductor has three intentionally separate production modes. Start here,
select one mode, and then load only that mode's skill and reference files. Do
not load the manga, story, and song manuals together.

## Orient from a folder or repository link

From the repository root:

```bash
uv sync
uv run mediaconductor modes --json
uv run mediaconductor where --json
```

Choose exactly one route:

| Requested result | Skill to read | Setup command |
|---|---|---|
| Manga/manhwa/webtoon recap | [`../skills/manga-video/SKILL.md`](../skills/manga-video/SKILL.md) | `uv run mediaconductor setup --mode manga-video` |
| Written story to narrated AI video | [`../skills/ai-story/SKILL.md`](../skills/ai-story/SKILL.md) | `uv run mediaconductor setup --mode ai-story` |
| Song generation or timed lyric video | [`../skills/song-video/SKILL.md`](../skills/song-video/SKILL.md) | `uv run mediaconductor setup --mode song-video` |

After setup, verify only the selected mode:

```bash
uv run mediaconductor doctor --mode <mode> --json
uv run mediaconductor commands --mode <mode> --json --full
```

The detailed manga-only operating manual is
[`manga-video-guide.md`](manga-video-guide.md). Story and song agents must not
load it.

## MCP contract

Register one scoped server and restart it when switching modes:

```bash
mediaconductor mcp --mode <manga-video|ai-story|song-video> --allow-root <workspace>
```

The no-mode `mediaconductor mcp` server is a small discovery/router surface.
Long-running operations use its typed `job_start` tool and are followed with
`job_status`; raw command lines are not accepted. Publishing is always an
explicit, rights-gated stage and never part of Story or Song `--stage all`.
The repeatable `--allow-root` boundary applies to direct tool paths, nested
`job_start` arguments, and external files referenced by Story/Song manifests.
Omitting it confines the server to its startup directory. It is a same-user
stdio safety boundary, not a replacement for an operating-system sandbox.

Machine-readable conventions remain stable for 2.x compatibility:

- Exit code `0` means success, `1` means validation/runtime failure, `2` means
  invalid CLI use, and `3` means an artifact exists but QA approval is needed.
- Generation emits `MANGAEASY_RESULT {...}` and progress emits
  `MANGAEASY_PROGRESS current/total label`.
- `MANGAEASY_ROOT` and the other `MANGAEASY_*` names remain supported so old
  installations do not silently move large model caches.
- The legacy equivalents `mangaeasy where --json`,
  `mediaconductor commands --json`, and `mediaconductor mcp` remain available.

Manga agents can discover existing projects with `mediaconductor library-list
--json`; Story and Song projects are manifest-driven and should use their
mode-specific skill instead.
