# mangaEasy

> Turn manga panels (or any folder of images + narration) into narrated videos — one installable tool, one command.

[![GitHub Release](https://img.shields.io/github/v/release/tawhidUnhappy/mangaEasy?label=download&color=blue)](https://github.com/tawhidUnhappy/mangaEasy/releases/latest)
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
- [Download — no Python needed](#download--no-python-needed)
- [Requirements](#requirements)
- [Install (developers / advanced)](#install-developers--advanced)
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
- **One-command AI tool install** — `mangaeasy install-tool index-tts` or
  `mangaeasy install-tool got-ocr2` builds isolated model environments for you.
- **General item-based video pipeline** — point it at numbered folders of images +
  `narration.json` and get per-item videos plus an optional joined long video.
- **Isolated AI tools** — Kokoro / IndexTTS / MAGI / GOT-OCR run in their own `uv`
  environments so their Torch/CUDA stacks never clash with the core install.
- **Hardware-aware everything** — `--encoder auto` picks NVENC, AMF, Quick Sync,
  VideoToolbox, or falls back to `libx264`; `--tts auto` uses IndexTTS (voice
  cloning) on NVIDIA GPU machines and Kokoro on CPU machines.
- **Direct YouTube upload** — connect your channel once (your own free
  Google OAuth client, see [docs/youtube.md](docs/youtube.md)) and upload
  finished videos from the app, the CLI (`mangaeasy youtube-upload`), or an
  AI assistant. Resumable uploads with progress; tokens stay in the app's
  own data folder.
- **Legacy manga workflow included** — MangaDex download, page-cutting and
  narration web editors, watermarking, PDF export, and more.
- **Cross-platform** — Windows, macOS, and Linux.

## Download — nothing to install, nothing left behind

The easiest way to get mangaEasy is to download the desktop app from the
[**Releases page**](https://github.com/tawhidUnhappy/mangaEasy/releases/latest).
It's a self-contained Electron app with the Python backend bundled inside —
**no Python, Node, or ffmpeg install required**, and it auto-detects your
GPU (NVIDIA CUDA, Apple Silicon, or CPU-only) with no setup. On first launch
the Setup tab offers a one-time ~100 MB **Download core tools** (ffmpeg and
friends) into the app's own data folder — on all three platforms, macOS
included. Plain `git` is the one thing still expected on your system (used
to fetch the optional AI tools).

**Windows**

| File | Type | How to use |
|---|---|---|
| `mangaEasy-X.Y.Z-windows-x64-portable.exe` | Portable | No install — just run it (SmartScreen: *More info → Run anyway*) |

There is deliberately no Windows installer (`.exe` setup). An installer would
write a registry uninstall key and a Start Menu shortcut outside wherever you
put the app, and those wouldn't go away if you just delete the folder. The
portable build keeps the promise below literal: drop the folder anywhere,
run the exe, delete the folder when you're done.

**macOS**

| File | Type | How to use |
|---|---|---|
| `mangaEasy-X.Y.Z-mac-arm64.dmg` | Installer | Drag to Applications |
| `mangaEasy-X.Y.Z-mac-arm64.zip` | Portable | Extract, `xattr -cr mangaEasy.app`, run it |

**Linux**

| File | Type | How to use |
|---|---|---|
| `mangaEasy-X.Y.Z-linux-x86_64.AppImage` | Portable | `chmod +x`, run it — no install |
| `mangaEasy-X.Y.Z-linux-amd64.deb` | Installer | `sudo dpkg -i mangaEasy-*.deb` |
| `mangaEasy-X.Y.Z-linux-x64.tar.gz` | Portable | Extract, run the binary inside |

Launch the app and use the **Setup** tab to install whichever AI tools you
want (index-tts, kokoro, magi-v3, got-ocr2) — each downloads on demand into
the app's own self-contained data folder, never your home directory.

**Everything mangaEasy ever writes lives in one data folder** — AI tool
installs, model weights, Hugging Face/torch/uv caches, app state, logs, even
Electron's own browser caches:

| Platform | Data folder |
|---|---|
| Windows (portable) | next to the `.exe` — the folder *is* the install |
| macOS | `~/Library/Application Support/mangaEasy` |
| Linux | `~/.local/share/mangaEasy` |

The Setup tab's **About** section shows the exact path with an Open button.
Delete that folder (plus the app itself) and nothing is left anywhere else
on the machine. Override the location with the `MANGAEASY_ROOT` environment
variable if you want it somewhere specific.

> **macOS Gatekeeper note:** the first time you may need to right-click → Open
> to bypass the "unidentified developer" warning, or run
> `xattr -cr mangaEasy.app` in the terminal after extracting.

See [docs/install.md](docs/install.md) for full installation instructions.

### Using mangaEasy with an AI assistant

The whole tool surface is one non-interactive CLI, so AI assistants (Claude
Code, Cursor, any agent with a shell) can drive it directly —
**[docs/ai-guide.md](docs/ai-guide.md)** is the complete operating manual:
machine-readable discovery (`mangaeasy commands --json`, `where --json`,
`library-list --json`), stable `MANGAEASY_RESULT`/`MANGAEASY_PROGRESS`
output markers, exit codes, and copy-paste recipes. There's also a built-in
MCP server: register `mangaeasy mcp` and the pipeline shows up as typed
tools in any MCP-capable assistant.

---

## Requirements

**Using the desktop app from the Releases page:** just `git` — used to fetch
the optional AI tools. Python ships inside the app on every platform;
ffmpeg/ffprobe/uv/git-lfs download once via the Setup tab's
**Download core tools** button (all platforms, macOS included).

**Running from source / as a CLI tool (developers):**

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
root — it syncs Python deps, builds the desktop app on first run, and opens
`mangaeasy app` for you in one step.

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

Opens the native Electron desktop app (the same app the Releases page ships)
with everything in one place:

- **Setup** — checks git / GPU, shows which AI tools are installed, installs
  or updates them with one click while streaming the logs live in the
  built-in terminal.
- **Project** — pick your project folder with a real folder dialog and edit
  `config.json` / `config.system.json` with forms (or the raw JSON via a
  Monaco editor), no manual JSON wrangling.
- **Make a video** / **Batch videos** — choose your manga folder and output
  folder with Browse… buttons, pick what to run, press Start; watch progress
  in the built-in terminal. Your folder choices are remembered between launches.
- **Editor** — launch the panel / narration web editors with one click,
  embedded right in the app window.

See [docs/app.md](docs/app.md) for details.

## Install the AI tools (one command)

The heavy AI models (TTS, panel detection) live in their **own isolated
environments** so they never break the main install. Check what your system has
and provision what's missing:

```bash
mangaeasy doctor                  # prerequisite + tool status report
mangaeasy install-tool index-tts  # IndexTTS-2 voice-cloning TTS (clone + env + model)
mangaeasy install-tool magi-v3    # MAGI v3 manga panel detection (env + adapter)
mangaeasy install-tool got-ocr2   # GOT-OCR 2.0 panel OCR (HF model + env)
```

Tools install into `<data folder>/.mangaeasy/tools` and are found automatically from any
folder. The installer **detects your hardware**: NVIDIA GPU → CUDA builds,
Apple Silicon → MPS-enabled builds, otherwise CPU builds that work on any
machine (force one with `--cuda` / `--cpu`). Other flags: `--ref <branch/tag>`
to pin a version, `--skip-model` to skip the big weight download, `--update`
to pull the latest version of an already-installed tool. The same installs
(and updates) are available as buttons in `mangaeasy app`'s Setup tab.

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
| **Setup & app** | `app` (desktop control center), `doctor` (environment report), `install-tool` (provision AI tools from GitHub/Hugging Face). |
| **Video pipeline** | `video`, `video-audio`, `video-render`, `video-join`, `video-check`, `video-validate`, and audio/clean helpers — the general image-folder workflow. |
| **External tools** | `tools` (show where envs resolve), `index-tts`, `got-ocr2`. |
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
<data folder>/.mangaeasy/tools/
  index-tts/      # IndexTTS-2  (cloned uv project + model checkpoints)
  magi-v3/        # MAGI v3 panel detection (generated env + detect_magi.py)
  got-ocr2/       # GOT-OCR 2.0 panel OCR (generated env + HF model)
  kokoro-82m/     # Kokoro TTS  (generated env, default voice af_heart)
```

Sibling folders next to your project (`./index-tts`, `./magi-v3`, `./got-ocr2`, ...) also work —
handy if you manage the tools yourself. Check what resolves:

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
$env:GOT_OCR2_ROOT  = "D:/got-ocr2"
```

```bash
# macOS / Linux
export KOKORO_ROOT=~/models/kokoro-82m
export INDEX_TTS_ROOT=~/models/index-tts
export GOT_OCR2_ROOT=~/models/got-ocr2
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

- [Installation guide](docs/install.md) — standalone download, uv tool, and from-source options
- [The desktop app](docs/app.md) — the `mangaeasy app` control center
- [Installing AI tools](docs/install-tools.md) — what `install-tool` sets up for each tool
- [Architecture](docs/architecture.md) — how the package and external tools fit together
- [External tools](docs/external-tools.md) — Kokoro, IndexTTS, MAGI, GOT-OCR
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
