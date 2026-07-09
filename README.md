# mangaEasy

> Turn manga panels (or any folder of images + narration) into narrated videos — one installable tool, one command.

[![GitHub Release](https://img.shields.io/github/v/release/tawhidUnhappy/mangaEasy?label=download&color=blue)](https://github.com/tawhidUnhappy/mangaEasy/releases/latest)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Install with uv](https://img.shields.io/badge/install-uv%20tool-261230.svg)](https://docs.astral.sh/uv/)

`mangaeasy` is a batteries-included pipeline for building narrated videos from
image panels. It downloads chapters, crops panels, verifies them, takes a
narration script, generates text-to-speech audio, renders a video per item, and
stitches everything into one long video. Heavy AI models (TTS, panel detection)
stay in their **own isolated environments**, so the core tool installs fast and
stays conflict-free.

**mangaEasy is a CLI + MCP tool built for LLM agents — there is no GUI.** New to
the repo? Open **[START_HERE.md](START_HERE.md)**. Everything is exposed through
a single command:

```console
$ mangaeasy --help
mangaeasy 1.0.0 - manga & image-to-video automation

Usage:
  mangaeasy <command> [args...]
  mangaeasy <command> --help     Show a command's own options
  mangaeasy --version
...
```

---

## Contents

- [Features](#features)
- [Install — nothing left behind](#install--nothing-left-behind)
- [Requirements](#requirements)
- [Install the AI tools (one command)](#install-the-ai-tools-one-command)
- [Quick start: image folders → video](#quick-start-image-folders--video)
- [The `mangaeasy` command](#the-mangaeasy-command)
- [External AI tools](#external-ai-tools)
- [Manga chapter workflow](#manga-chapter-workflow)
- [Configuration](#configuration)
- [Output layout](#output-layout)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

---

## Features

- **One command, many tools** — `mangaeasy <subcommand>`, discoverable via `--help`.
- **Agent-native** — every command has a `--json` / machine-readable contract,
  and `mangaeasy mcp` exposes the same engine as typed MCP tools for an agent host.
- **One-command setup** — `mangaeasy setup` provisions a fresh machine end to
  end (core binaries, AI tool envs, model downloads), GPU-aware and resumable
  ([docs/setup.md](docs/setup.md)); `mangaeasy install-tool <name>` installs
  tools individually.
- **Full-series acquisition** — `mangaeasy download --url <mangadex url> --all`
  grabs a whole series politely (rate-limited, jittered, resumable), and
  `series-plan` slices it into 12-chapter upload batches and tracks what's
  published.
- **General item-based video pipeline** — point it at numbered folders of images +
  `narration.json` and get per-item videos plus an optional joined long video.
- **Isolated AI tools** — Kokoro / IndexTTS / MAGI / DeepSeek-OCR 2 / Z-Image Turbo run in their own `uv`
  environments so their Torch/CUDA stacks never clash with the core install.
- **Hardware-aware everything** — `--encoder auto` picks NVENC, AMF, Quick Sync,
  VideoToolbox, or falls back to `libx264`; `--tts auto` (the default) uses
  IndexTTS (voice cloning) whenever your machine is ready for it — NVIDIA GPU,
  the `index-tts` tool installed, and a speaker reference WAV — falling back
  to Kokoro otherwise, so `mangaeasy video` always works out of the box.
- **Direct YouTube upload** — connect your channel once (your own free
  Google OAuth client, see [docs/youtube.md](docs/youtube.md)) and upload
  finished videos from the CLI (`mangaeasy youtube-upload`) or an AI assistant.
  Resumable uploads with progress; tokens stay in the install's own data folder.
- **Cross-platform** — Windows, macOS, and Linux.

## Install — nothing left behind

Get the `mangaeasy` command one of three ways (full details in
[docs/install.md](docs/install.md)):

```bash
# 1. uv tool (recommended)
uv tool install git+https://github.com/tawhidUnhappy/mangaEasy.git

# 2. from source
git clone https://github.com/tawhidUnhappy/mangaEasy.git && cd mangaEasy && uv sync

# 3. a frozen release build (no Python) — from the Releases page:
#    mangaeasy-windows.zip / mangaeasy-macos-arm64.zip / mangaeasy-linux.tar.gz
```

Then provision everything in one go (downloads land in the install's own
self-contained data folder, never your home dir):

```bash
mangaeasy setup      # core binaries + GPU-appropriate AI tools + models
                     # (--minimal / --all / --skip <tool> — see docs/setup.md)
```

GPU acceleration (NVIDIA CUDA, Apple Silicon) is auto-detected, with CPU
fallback everywhere. **Everything mangaEasy ever writes lives in one data
folder** — tool installs, model weights, HF/torch/uv caches, logs — so
deleting it leaves nothing behind. Override its location with `MANGAEASY_ROOT`.

### Using mangaEasy with an AI assistant

The whole tool surface is one non-interactive CLI, so AI assistants (Claude
Code, Cursor, any agent with a shell) can drive it directly —
**[docs/ai-guide.md](docs/ai-guide.md)** is the complete operating manual:
machine-readable discovery (`mangaeasy commands --json`, `where --json`,
`library-list --json`), stable `MANGAEASY_RESULT`/`MANGAEASY_PROGRESS`
output markers, exit codes, and copy-paste recipes. There's also a built-in
MCP server: register `mangaeasy mcp` and the pipeline shows up as typed
tools in any MCP-capable assistant. The full production workflow (MangaDex
URL → narrated, thumbnailed, uploaded recap series in 12-chapter batches) is
encoded as an agent skill in
[.claude/skills/manga-recap/SKILL.md](.claude/skills/manga-recap/SKILL.md) —
Claude Code discovers it automatically when working in this repo.

---

## Requirements

**Using a frozen release build:** just `git` (used to fetch the optional AI
tools); Python ships inside the build, and `mangaeasy bootstrap-tools`
downloads ffmpeg/ffprobe/uv/git-lfs on demand.

**Running from source / as a CLI tool:**

- Python **3.10+**
- [`uv`](https://docs.astral.sh/uv/) (recommended installer/runner) — or let
  `mangaeasy bootstrap-tools` vendor a copy into the self-contained data folder
- `git` (plain git stays a real system prerequisite either way)
- *(optional)* external TTS / detection tools — installed for you by
  [`mangaeasy install-tool`](#install-the-ai-tools-one-command)

**A GPU is not required.** Everything runs on plain CPUs (Windows, macOS,
Linux): video encoding falls back to `libx264`, and `mangaeasy doctor` /
`install-tool` auto-detect NVIDIA CUDA, Apple Silicon (MPS), or CPU-only and
configure themselves accordingly — no flags to pass. With a GPU you get
hardware video encoding and much faster TTS/panel-detection; without one,
everything still works, just slower.

## Install (developers / advanced)

### As a uv tool

```bash
uv tool install git+https://github.com/tawhidUnhappy/mangaEasy.git
```

This puts a single `mangaeasy` command on your `PATH`. Update later with:

```bash
uv tool upgrade mangaeasy
```

### Run once without installing

```bash
uvx --from git+https://github.com/tawhidUnhappy/mangaEasy.git mangaeasy --help
```

### From source (development)

```bash
git clone https://github.com/tawhidUnhappy/mangaEasy.git
cd mangaEasy
uv sync
uv run mangaeasy --help
```

Or just run `./run.sh` (macOS/Linux) or `run.bat` (Windows) from the repo
root — it syncs Python deps and prints the command list.

Optional extras (only if you want heavy models *inside* the main env instead of
managed external tool environments — most users should not need these):

```bash
uv sync --extra whisper   # faster-whisper
uv sync --extra ml        # torch, transformers, opencv, MAGI deps, ...
uv sync --extra all        # everything above
```

## Install the AI tools (one command)

The heavy AI models (TTS, panel detection, OCR, image generation) live in their **own isolated
environments** so they never break the main install. Check what your system has
and provision what's missing:

```bash
mangaeasy doctor                       # prerequisite + tool status report
mangaeasy install-tool index-tts       # IndexTTS-2 voice-cloning TTS (clone + env + model)
mangaeasy install-tool magi-v3         # MAGI v3 manga panel detection (env + adapter)
mangaeasy install-tool deepseek-ocr2   # DeepSeek-OCR 2 panel/document OCR (HF model + env)
mangaeasy install-tool z-image-turbo   # Z-Image Turbo text-to-image (thumbnails; ~33 GB)
```

Tools install into `<data folder>/.mangaeasy/tools` and are found automatically from any
folder. The installer **detects your hardware**: NVIDIA GPU → CUDA builds,
Apple Silicon → MPS-enabled builds, otherwise CPU builds that work on any
machine (force one with `--cuda` / `--cpu`). Other flags: `--ref <branch/tag>`
to pin a version, `--skip-model` to skip the big weight download, `--update`
to pull the latest version of an already-installed tool.

See [docs/install-tools.md](docs/install-tools.md) for what each installer does.

## Quick start: image folders → video

Lay out your content as numbered item folders, each with a `narration.json` and a
`panels/` directory:

```text
content/
  01/
    narration.json
    panels/
      panel_001.png
      panel_002.png
  02/
    narration.json
    panels/
```

`narration.json` is an array pairing each image with its narration:

```json
[
  { "image": "panel_001.png", "narration": "Our story begins on a quiet night." },
  { "image": "panel_002.png", "narration": "But quiet never lasts for long." }
]
```

Check the inputs, then build everything (TTS audio → per-item videos → one long video):

```bash
# 1. Validate panels + narration before spending GPU time
mangaeasy video-check --project-root content --item-range 01-24 --strict

# 2. Generate audio, render item videos, and join into one long video
mangaeasy video --project-root content --project-name my_project \
    --item-range 01-24 --build-long-video
```

Prefer to run the stages yourself?

```bash
mangaeasy video-audio    --project-root content --item-range 01-24
mangaeasy video-render   --project-root content --item-range 01-24
mangaeasy video-join     --project-root content --project-name my_project --item-range 01-24
mangaeasy video-validate --project-root content --project-name my_project --item-range 01-24 --require-long
```

> **TTS engine:** `mangaeasy video` picks the best engine for your machine —
> **IndexTTS** (voice cloning, highest quality) when you have an NVIDIA GPU and
> have run `mangaeasy install-tool index-tts`, otherwise **Kokoro** (light,
> great on CPU). Force one with `--tts indextts` / `--tts kokoro`. IndexTTS
> clones the voice from a reference WAV (`config.system.json → tts.speaker_wav`
> or `--speaker-wav`); Kokoro uses the built-in `af_heart` voice.

## The `mangaeasy` command

Run `mangaeasy` (or `mangaeasy --help`) to list every subcommand, grouped by
purpose. Add `--help` to any subcommand for its own options:

```bash
mangaeasy video --help
mangaeasy download --help
```

Command groups:

| Group | What it covers |
|-------|----------------|
| **Setup** | `where`, `commands`, `doctor` (environment report), `bootstrap-tools`, `install-tool` (provision AI tools), `library-list`, `mcp`. |
| **Video pipeline** | `video`, `video-audio`, `video-render`, `video-join`, `video-check`, `video-validate`, and audio/clean helpers — the general image-folder workflow. |
| **YouTube** | `youtube-auth`, `youtube-status`, `youtube-upload`, `youtube-delete`. |
| **External tools** | `tools` (show where envs resolve), `index-tts`, `deepseek-ocr2`, `zimage`. |
| **Manga: acquire** | `download`, `webtoon-split`, `page-split`, `gutter-split` — crop → verify → narrate ([guide](docs/operate/crop-verify-narrate.md)). |
| **Manga: export** | `to-pdf`, `to-pdf-lossless`, `convert-images`, `watermark`, `ai-zip`. |

## External AI tools

The heavy model tools are kept in **their own isolated `uv` environments** so
their large, conflicting dependency stacks never touch the core install. The
easiest way to get them is [`mangaeasy install-tool`](#install-the-ai-tools-one-command),
which puts them in the managed folder:

```text
<data folder>/.mangaeasy/tools/
  index-tts/       # IndexTTS-2  (cloned uv project + model checkpoints)
  magi-v3/         # MAGI v3 panel detection (generated env + detect_magi.py)
  deepseek-ocr2/   # DeepSeek-OCR 2 panel/document OCR (generated env + HF model)
  kokoro-82m/      # Kokoro TTS  (generated env, default voice af_heart)
  z-image-turbo/   # Z-Image Turbo text-to-image (generated env + HF model)
```

Check what resolves:

```bash
mangaeasy tools
```

Override locations with environment variables (handy when the tool is installed
globally but your models live elsewhere):

```bash
# Windows (PowerShell)
$env:KOKORO_ROOT    = "D:/kokoro-82m"
$env:INDEX_TTS_ROOT = "D:/index-tts"
$env:MAGI_V3_ROOT   = "D:/magi-v3"
$env:DEEPSEEK_OCR2_ROOT = "D:/deepseek-ocr2"
```

```bash
# macOS / Linux
export KOKORO_ROOT=~/models/kokoro-82m
export INDEX_TTS_ROOT=~/models/index-tts
export DEEPSEEK_OCR2_ROOT=~/models/deepseek-ocr2
```

See [docs/external-tools.md](docs/external-tools.md) for how each tool is invoked.

## Manga chapter workflow

End-to-end manga work is: download → crop panels → verify → write narration →
build video. The crop → verify → narrate loop has its own guide:
[docs/operate/crop-verify-narrate.md](docs/operate/crop-verify-narrate.md); the
full recap production recipe is
[docs/recap-video-playbook.md](docs/recap-video-playbook.md).

```bash
# copy the examples and edit them in your project folder
cp config.example.json config.json
cp config.system.example.json config.system.json

mangaeasy download                                    # fetch a chapter from MangaDex
mangaeasy webtoon-split --project-root library/<Proj> --items 01   # crop (webtoons)
mangaeasy page-split    --project-root library/<Proj> --items 01   # crop (paged manga)
# ...verify the crops, write narration.json, then build:
mangaeasy video         --project-root library/<Proj> --items 01 --build-long-video
```

`download` records where each manga came from in
`library/<name>/manga.json` (source link, title, downloaded chapters), and
`mangaeasy library-list` shows it — so the link isn't lost when you point
`config.json` at the next manga.

If the tool is installed globally but your project files live elsewhere, run
commands from the project folder or set:

```bash
$env:MANGAEASY_PROJECT_ROOT = "D:/my-manga-project"   # PowerShell
export MANGAEASY_PROJECT_ROOT=~/my-manga-project       # bash
```

## Configuration

| File | Purpose | Tracked? |
|------|---------|----------|
| `config.example.json` | Template for per-manga settings (title, name, chapter). | ✅ committed |
| `config.system.example.json` | Template for machine/render settings (resolution, fps, encoder, ports, paths). | ✅ committed |
| `config.json` | Your real per-manga config. | ❌ git-ignored |
| `config.system.json` | Your real machine config. | ❌ git-ignored |

The general video pipeline (`mangaeasy video …`) is driven entirely by
command-line flags and does **not** require these config files — they are only
used by the manga chapter tools.

## Output layout

The video pipeline writes alongside your project:

```text
audio/<project>/                       # generated narration audio
output/<project>/items/                # one video per item
output/<project>/<project>_full.mp4    # the joined long video
work/<project>/                        # scratch / intermediates
```

## Documentation

- [**START_HERE.md**](START_HERE.md) — the repo entry map (read this first)
- [Installation guide](docs/install.md) — uv tool, frozen release, and from-source options
- [AI/CLI guide](docs/ai-guide.md) — the complete operating manual + machine-readable contract
- [Crop → verify → narrate](docs/operate/crop-verify-narrate.md) — the core loop
- [Recap video playbook](docs/recap-video-playbook.md) — full end-to-end production recipe
- [Installing AI tools](docs/install-tools.md) — what `install-tool` sets up for each tool
- [Architecture](docs/architecture.md) — how the package and external tools fit together
- [External tools](docs/external-tools.md) — Kokoro, IndexTTS, MAGI, DeepSeek-OCR 2, Z-Image Turbo
- [Thumbnail guide](docs/thumbnail.md) — Z-Image Turbo prompts and checks for recap thumbnails
- [Publishing](docs/publishing.md) — release checklist and CI workflow

## Contributing

Issues and pull requests are welcome at
<https://github.com/tawhidUnhappy/mangaEasy>. For local development:

```bash
uv sync
uv run mangaeasy --help
# byte-compile the package as a quick sanity check
uv run python -m compileall mangaeasy
```

## License

[MIT](LICENSE) © mangaEasy contributors.
