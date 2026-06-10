# mangaEasy

> Turn manga panels (or any folder of images + narration) into narrated videos — one installable tool, one command.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Install with uv](https://img.shields.io/badge/install-uv%20tool-261230.svg)](https://docs.astral.sh/uv/)

`mangaeasy` is a batteries-included pipeline for building narrated videos from
image panels. It downloads/cuts pages, helps you write narration, generates
text-to-speech audio, renders a video per item, and stitches everything into one
long video. Heavy AI models (TTS, panel detection) stay in their **own isolated
environments**, so the core tool installs fast and stays conflict-free.

Everything is exposed through a single command:

```console
$ mangaeasy --help
mangaeasy 0.3.0 - manga & image-to-video automation

Usage:
  mangaeasy <command> [args...]
  mangaeasy <command> --help     Show a command's own options
  mangaeasy --version
...
```

---

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Install](#install)
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
- **General item-based video pipeline** — point it at numbered folders of images +
  `narration.json` and get per-item videos plus an optional joined long video.
- **Isolated AI tools** — Kokoro / IndexTTS / F5-TTS / MAGI run in their own `uv`
  environments so their Torch/CUDA stacks never clash with the core install.
- **Hardware-aware encoding** — `--encoder auto` picks NVENC, AMF, Quick Sync,
  VideoToolbox, or falls back to `libx264`. `--device auto` uses CUDA when present.
- **Legacy manga workflow included** — MangaDex download, page-cutting and
  narration web editors, watermarking, PDF export, and more.
- **Cross-platform** — Windows, macOS, and Linux.

## Requirements

- Python **3.10+**
- [`uv`](https://docs.astral.sh/uv/) (recommended installer/runner)
- **FFmpeg** and **FFprobe** on your `PATH`
- *(optional)* an NVIDIA / AMD / Intel / Apple GPU encoder exposed by your FFmpeg build
- *(optional)* external TTS / detection tools as sibling `uv` projects — see
  [External AI tools](#external-ai-tools)

## Install

### As a tool (recommended)

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

Optional extras (only if you want heavy models *inside* the main env instead of
as isolated sibling tools — most users should not need these):

```bash
uv sync --extra whisper   # faster-whisper
uv sync --extra ml        # torch, transformers, opencv, MAGI deps, ...
uv sync --extra all        # everything above
```

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

> Audio generation uses Kokoro by default and expects a sibling `kokoro-82m`
> environment. See [External AI tools](#external-ai-tools).

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
| **Video pipeline** | `video`, `video-audio`, `video-render`, `video-join`, `video-check`, `video-validate`, and audio/clean helpers — the general image-folder workflow. |
| **External tools** | `tools` (show where envs resolve), `index-tts`, `f5-tts`. |
| **Manga: acquire** | `download`, `cut-page`, `panel-editor`, `gutter-split`, `process-panels`. |
| **Manga: narration** | `narration-editor`, `narration-editor-all`, `narration-review`, `join-narration`, `normalize-narration`, `clean-narration`, `backup-narration`, `rename-file`. |
| **Manga: render** | `render-video`, `add-bgm`, `join-chapters`, `timestamps`, `to-pdf`, `watermark`, `convert-images`, … |
| **Manga: chapters** | `init-chapter`, `increment-chapter`, `reset-chapter`, `fix-name`, `clean-chapter`. |

## External AI tools

The heavy model tools are kept as **sibling `uv` projects** so their large,
conflicting dependency stacks never touch the core install. The recommended
workspace layout:

```text
workspace/
  mangaEasy/      # this project (or just the installed tool)
  kokoro-82m/     # Kokoro TTS  (uv project)
  index-tts/      # IndexTTS     (uv project, optional)
  f5-tts/         # F5-TTS       (uv project, optional)
  magi-v3/        # MAGI panel detection (uv project, optional)
```

`mangaeasy` auto-detects siblings by folder name. Check what resolves:

```bash
mangaeasy tools
```

Override locations with environment variables (handy when the tool is installed
globally but your models live elsewhere):

```bash
# Windows (PowerShell)
$env:KOKORO_ROOT    = "D:\kokoro-82m"
$env:INDEX_TTS_ROOT = "D:\index-tts"
$env:F5_TTS_ROOT    = "D:\f5-tts"
$env:MAGI_V3_ROOT   = "D:\magi-v3"
```

```bash
# macOS / Linux
export KOKORO_ROOT=~/models/kokoro-82m
export INDEX_TTS_ROOT=~/models/index-tts
```

See [docs/external-tools.md](docs/external-tools.md) for how each tool is invoked.

## Manga chapter workflow

The original chapter-based manga tools are still here for end-to-end manga work
(download → cut pages → write narration → render). These are **config-driven**:
they read `config.json` (per-manga settings) and `config.system.json`
(machine/render settings) from the folder you run them in.

```bash
# copy the examples and edit them in your project folder
cp config.example.json config.json
cp config.system.example.json config.system.json

mangaeasy download         # fetch a chapter from MangaDex
mangaeasy cut-page         # web editor: cut pages into panels
mangaeasy narration-editor # web editor: write narration
mangaeasy render-video     # render the chapter video
```

If the tool is installed globally but your project files live elsewhere, run
commands from the project folder or set:

```bash
$env:MANGAEASY_PROJECT_ROOT = "D:\my-manga-project"   # PowerShell
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

- [Architecture](docs/architecture.md) — how the package and external tools fit together
- [External tools](docs/external-tools.md) — Kokoro, IndexTTS, F5-TTS, MAGI
- [Publishing](docs/publishing.md) — release checklist

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
