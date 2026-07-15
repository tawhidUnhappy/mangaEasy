# Installing MediaConductor

MediaConductor is a CLI and MCP server for manga video, AI story, and song
video production. These are the three supported installation paths.

---

## Option 1 — Install with uv (recommended)

Requires [uv](https://docs.astral.sh/uv/) installed on your system.

```bash
uv tool install git+https://github.com/tawhidUnhappy/MediaConductor.git
mediaconductor --version
```

This puts `mediaconductor` on your `PATH`. The legacy `mangaeasy` alias is also
installed for existing automation. Update later:

```bash
uv tool upgrade media-conductor
```

Run without installing (useful for a quick test):

```bash
uvx --from git+https://github.com/tawhidUnhappy/MediaConductor.git mediaconductor --help
```

---

## Option 2 — Download a frozen release (no Python needed)

The [**Releases page**](https://github.com/tawhidUnhappy/MediaConductor/releases/latest)
ships a self-contained frozen build of the CLI per platform:

| Platform | File | Run |
|---|---|---|
| Windows | `media-conductor-windows.zip` | unzip → `MediaConductor\mediaconductor.exe --help` |
| macOS (Apple Silicon) | `media-conductor-macos-arm64.zip` | unzip → `xattr -cr MediaConductor.app` once → `MediaConductor.app/Contents/MacOS/mediaconductor --help` |
| Linux | `media-conductor-linux.tar.gz` | `tar xzf` → `MediaConductor/mediaconductor --help` |

No system Python is required — the build bundles it. The archives are unsigned
(free software, no paid certificate): on Windows SmartScreen click **More
info → Run anyway**; on macOS run the `xattr -cr` line above once.

---

## Option 3 — From source (contributors)

```bash
git clone https://github.com/tawhidUnhappy/MediaConductor.git
cd MediaConductor
uv sync
uv run mediaconductor --help
```

Or run `./run.sh` (macOS/Linux) / `run.bat` (Windows) from the repo root — it
runs `uv sync` and prints the command list. New to the code? Open
[CLAUDE.md](../CLAUDE.md).

Build a frozen release yourself with PyInstaller:

```bash
uv sync --group dev
uv run pyinstaller packaging/mediaconductor.spec
# Output: dist/MediaConductor/ (Windows/Linux) or dist/MediaConductor.app/ (macOS)
```

---

## First-run setup

Select one mode, then install only that mode's isolated dependencies
(details in [setup.md](setup.md)):

```bash
mediaconductor modes
mediaconductor setup --mode ai-story  # or manga-video / song-video
```

It vendors the core binaries (ffmpeg/uv/git-lfs), then installs the AI tool
envs + models that fit the machine: Kokoro TTS always; IndexTTS, MAGI v3,
DeepSeek-OCR 2 and Z-Image Turbo when an NVIDIA GPU is present. `--minimal`,
`--all`, `--skip <tool>`, `--dry-run` variants; safe to re-run (resumes).

Prefer picking pieces yourself?

```bash
mediaconductor doctor --mode ai-story --json
mediaconductor bootstrap-tools
mediaconductor install-tool kokoro-82m
mediaconductor install-tool index-tts
mediaconductor install-tool magi-v3
```

Everything MediaConductor writes — installed AI tools, models, settings, logs, and
(by default) your projects — lives under one data folder, so deleting it leaves
no trace. GPU acceleration (NVIDIA CUDA / Apple Silicon) is detected
automatically. Core video tools and selected models support CPU fallback;
GPU-only or impractically slow tools are reported by `doctor` for the chosen
mode. Override the data root with the `MANGAEASY_ROOT` environment variable.

### Where your data lives

| Platform | Data folder (when `MANGAEASY_ROOT` is unset) |
|---|---|
| Windows (frozen) | next to the exe |
| macOS | `~/Library/Application Support/mangaEasy` (legacy-compatible path) |
| Linux | `~/.local/share/mangaEasy` (or `$XDG_DATA_HOME/mangaEasy`; legacy-compatible path) |
| Dev checkout | the repo root |

---

## Updating

- **uv install**: `uv tool upgrade media-conductor`.
- **Frozen release**: download the newer archive and replace the old one; your
  data folder is separate, so installed tools/projects carry over.
- **Source**: `git pull && uv sync`.
