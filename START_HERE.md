# START HERE — mangaEasy

**You just opened `D:\mangaEasy`. Read this file first.** It orients any
LLM or human to what this repo is, how it's laid out, and which doc to open for
the job you're doing. Every other doc is reachable from here.

## What this is

mangaEasy turns a manga/webtoon chapter (a folder of images) into a narrated
YouTube "recap" video, and chains chapters into one long video with background
music.

**mangaEasy is a CLI + MCP tool built for LLM agents — there is no GUI.** The
entire surface is:

- **CLI** — a single `mangaeasy` command with ~50 subcommands, each with a
  `--json` / machine-readable contract (see [docs/ai-guide.md](docs/ai-guide.md)).
- **MCP server** — `mangaeasy mcp` exposes the same engine as typed tools for
  an agent host. Same backend, no extra surface.
- **Agent skill** — [.claude/skills/manga-recap/SKILL.md](.claude/skills/manga-recap/SKILL.md)
  encodes the full MangaDex-URL → published-recap-series workflow; Claude Code
  picks it up automatically in this repo, and any agent can follow it as a
  runbook.

Everything mangaEasy writes stays under one data folder — it is designed to be
**self-contained** (models, tools, output all in-tree, nothing scattered on the
host), so an agent can drive the whole pipeline from one directory.

## The pipeline (the mental model everything follows)

```
download → CROP → VERIFY → read → NARRATE → audio → render → join → BGM → thumbnail → upload
          └──────────── the core loop ────────────┘   └──────── the video pipeline ────────┘
```

The **core loop** (crop → verify → see → narrate) is the heart of the product
and the best place to start understanding the code:
[docs/operate/crop-verify-narrate.md](docs/operate/crop-verify-narrate.md).

## Which doc for which job

| I want to… | Read |
|---|---|
| Understand the whole thing (this) | **START_HERE.md** |
| Set up a fresh machine in one command | [docs/setup.md](docs/setup.md) |
| Produce a recap series as an agent (URL → uploads) | [.claude/skills/manga-recap/SKILL.md](.claude/skills/manga-recap/SKILL.md) |
| Crop panels, verify them, write narration | [docs/operate/crop-verify-narrate.md](docs/operate/crop-verify-narrate.md) |
| Produce a full recap video end-to-end | [docs/recap-video-playbook.md](docs/recap-video-playbook.md) |
| Drive the CLI from a script/agent | [docs/ai-guide.md](docs/ai-guide.md) |
| Modify the code (conventions, invariants) | [CLAUDE.md](CLAUDE.md) |
| Understand a specific package | that package's `README.md` (see the map below) |
| See the architecture / data layout | [docs/architecture.md](docs/architecture.md), [CLAUDE.md](CLAUDE.md) |
| Install external AI tools (MAGI, TTS, …) | [docs/install-tools.md](docs/install-tools.md), [docs/external-tools.md](docs/external-tools.md) |
| Set up / upload to YouTube | [docs/youtube.md](docs/youtube.md) |
| Know why the repo is structured this way | [docs/history/reorg-plan.md](docs/history/reorg-plan.md) |

## First two commands on any machine

```bash
mangaeasy where --json      # resolved data/tool paths + version — run this first
mangaeasy commands --json   # the full machine-readable command catalog
mangaeasy doctor --json     # ffmpeg / GPU / external-tool status
```

No command ever prompts for interactive input; all support `--help`. The
machine-readable contract (JSON modes, `MANGAEASY_RESULT` / `MANGAEASY_PROGRESS`
markers, exit codes) is specified in [docs/ai-guide.md](docs/ai-guide.md).

## Code map — `mangaeasy/`

Every package directory has its own `README.md` with entry points and gotchas.
Grouped by pipeline stage:

| Stage | Package | What it does |
|---|---|---|
| core | `mangaeasy/` (`cli.py`, `config.py`, `paths.py`, `runtime.py`, `library_scan.py`, `series_plan.py`, `mcp_server.py`) | command dispatch, config, path resolution, upload-batch planning, the MCP server |
| acquire | [`download/`](mangaeasy/download/) | fetch chapters (MangaDex) |
| acquire | [`panels/`](mangaeasy/panels/) | **crop**: `webtoon-split`, `page-split`, gutter/MAGI detection |
| produce | [`video_pipeline/`](mangaeasy/video_pipeline/) | the video build: audio → render → join → BGM |
| publish | [`youtube/`](mangaeasy/youtube/) | auth + resumable upload + delete |
| tools | [`tools/`](mangaeasy/tools/) | isolated external AI tool envs (MAGI, IndexTTS, Kokoro, DeepSeek-OCR, Z-Image) |
| shared | [`images/`](mangaeasy/images/), [`ocr/`](mangaeasy/ocr/), [`utils/`](mangaeasy/utils/) | image ops, OCR, archive/result helpers |

> The GUI (Electron desktop app + Flask web editors) and the older chapter-era
> render/audio commands **were removed** — mangaEasy is CLI + MCP only, driven
> by agents. [docs/history/legacy-inventory.md](docs/history/legacy-inventory.md)
> records what was removed and why.

## Where to change what (for a coding agent)

- **Add a CLI command** → write a module with a `main()` that does its own
  `argparse`, then add one line to `COMMANDS` in
  [mangaeasy/cli.py](mangaeasy/cli.py). Imports are lazy — don't import heavy
  deps at module top level. Emit `MANGAEASY_RESULT` from generation commands.
- **Change the crop loop** → [`mangaeasy/panels/`](mangaeasy/panels/).
- **Change the video build** → [`mangaeasy/video_pipeline/`](mangaeasy/video_pipeline/).
- **Expose a command over MCP** → add it to `TOOLS` in
  [mangaeasy/mcp_server.py](mangaeasy/mcp_server.py) (stdlib-only, shells out to
  the CLI).
- **Before committing** → `uv run pytest`, `uv run ruff check .`. CI runs those
  plus the doc-integrity checks
  ([tests/test_docs_crossref.py](tests/test_docs_crossref.py)).

The invariants you must not break (audio loudness, archive-before-overwrite,
the single `load_narration`, lazy imports, the CLI contract) are in
[CLAUDE.md](CLAUDE.md) — read it before your first edit.
