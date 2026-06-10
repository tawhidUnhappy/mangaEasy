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
- [The desktop app](#the-desktop-app)
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
- **Desktop app** — `mangaeasy app` opens a control center: install AI tools with
  one click, edit configs, run the pipeline, and launch editors with live logs.
- **One-command AI tool install** — `mangaeasy install-tool index-tts` clones the
  latest version from GitHub and builds its isolated environment for you.
- **General item-based video pipeline** — point it at numbered folders of images +
  `narration.json` and get per-item videos plus an optional joined long video.
- **Isolated AI tools** — Kokoro / IndexTTS / MAGI run in their own `uv`
  environments so their Torch/CUDA stacks never clash with the core install.
- **Hardware-aware everything** — `--encoder auto` picks NVENC, AMF, Quick Sync,
  VideoToolbox, or falls back to `libx264`; `--tts auto` uses IndexTTS (voice
  cloning) on NVIDIA GPU machines and Kokoro on CPU machines.
- **Legacy manga workflow included** — MangaDex download, page-cutting and
  narration web editors, watermarking, PDF export, and more.
- **Cross-platform** — Windows, macOS, and Linux.

## Requirements

- Python **3.10+**
- [`uv`](https://docs.astral.sh/uv/) (recommended installer/runner)
- **FFmpeg** and **FFprobe** on your `PATH`
- *(optional)* external TTS / detection tools — installed for you by
  [`mangaeasy install-tool`](#install-the-ai-tools-one-command)

**A GPU is not required.** Everything runs on plain CPUs (Windows, macOS,
Linux): video encoding falls back to `libx264`, and the AI tool installers
automatically pick CPU builds when no NVIDIA GPU is present. With an NVIDIA /
AMD / Intel / Apple GPU you get hardware video encoding, and with an NVIDIA GPU
the TTS and panel-detection models run much faster — but none of it is needed
to use the tool.

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

## The desktop app

```bash
mangaeasy app
```

Opens a native window (falls back to your browser with `--browser` or when no GUI
backend is available) with everything in one place:

- **Setup** — checks git / uv / FFmpeg / GPU, shows which AI tools are installed,
  and installs them with one click while streaming the logs live.
- **Project** — pick your project folder and edit `config.json` /
  `config.system.json` with forms, no manual JSON wrangling.
- **Run** — run the full video pipeline (or any single step) and chapter commands
  with dropdowns and checkboxes; watch progress in the built-in log console.
- **Editors** — launch the panel / narration web editors with one click.

See [docs/app.md](docs/app.md) for details.

## Install the AI tools (one command)

The heavy AI models (TTS, panel detection) live in their **own isolated
environments** so they never break the main install. Check what your system has
and provision what's missing:

```bash
mangaeasy doctor                  # prerequisite + tool status report
mangaeasy install-tool index-tts  # IndexTTS-2 voice-cloning TTS (clone + env + model)
mangaeasy install-tool magi-v3    # MAGI v3 manga panel detection (env + adapter)
```

Tools install into `~/.mangaeasy/tools` and are found automatically from any
folder. The installer **detects your hardware**: with an NVIDIA GPU it uses
CUDA builds, otherwise it picks CPU builds that work on any machine (force
either with `--cuda` / `--cpu`). Other flags: `--ref <branch/tag>` to pin a
version, `--skip-model` to skip the big weight download. The same installs are
available as buttons in `mangaeasy app`.

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
| **Setup & app** | `app` (desktop control center), `doctor` (environment report), `install-tool` (provision AI tools from GitHub). |
| **Video pipeline** | `video`, `video-audio`, `video-render`, `video-join`, `video-check`, `video-validate`, and audio/clean helpers — the general image-folder workflow. |
| **External tools** | `tools` (show where envs resolve), `index-tts`. |
| **Manga: acquire** | `download`, `cut-page`, `panel-editor`, `gutter-split`, `process-panels`. |
| **Manga: narration** | `narration-editor`, `narration-editor-all`, `narration-review`, `join-narration`, `normalize-narration`, `clean-narration`, `backup-narration`, `rename-file`. |
| **Manga: render** | `render-video`, `add-bgm`, `join-chapters`, `timestamps`, `to-pdf`, `watermark`, `convert-images`, … |
| **Manga: chapters** | `init-chapter`, `increment-chapter`, `reset-chapter`, `fix-name`, `clean-chapter`. |

## External AI tools

The heavy model tools are kept in **their own isolated `uv` environments** so
their large, conflicting dependency stacks never touch the core install. The
easiest way to get them is [`mangaeasy install-tool`](#install-the-ai-tools-one-command),
which puts them in the managed folder:

```text
~/.mangaeasy/tools/
  index-tts/      # IndexTTS-2  (cloned uv project + model checkpoints)
  magi-v3/        # MAGI v3 panel detection (generated env + detect_magi.py)
  kokoro-82m/     # Kokoro TTS  (generated env, default voice af_heart)
```

Sibling folders next to your project (`./index-tts`, `./magi-v3`, …) also work —
handy if you manage the tools yourself. Check what resolves:

```bash
mangaeasy tools
```

Override locations with environment variables (handy when the tool is installed
globally but your models live elsewhere):

```bash
# Windows (PowerShell)
$env:KOKORO_ROOT    = "D:\kokoro-82m"
$env:INDEX_TTS_ROOT = "D:\index-tts"
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

- [The desktop app](docs/app.md) — the `mangaeasy app` control center
- [Installing AI tools](docs/install-tools.md) — what `install-tool` sets up for each tool
- [Architecture](docs/architecture.md) — how the package and external tools fit together
- [External tools](docs/external-tools.md) — Kokoro, IndexTTS, MAGI
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
