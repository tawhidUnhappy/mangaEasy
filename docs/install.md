# Installing mangaEasy

**mangaEasy is a CLI + MCP tool for LLM agents — there is no GUI.** Three ways
to get the `mangaeasy` command, from easiest to most hands-on.

---

## Option 1 — Install with uv (recommended)

Requires [uv](https://docs.astral.sh/uv/) installed on your system.

```bash
uv tool install git+https://github.com/tawhidUnhappy/mangaEasy.git
mangaeasy --version
```

This puts a `mangaeasy` command on your `PATH`. Update later:

```bash
uv tool upgrade mangaeasy
```

Run without installing (useful for a quick test):

```bash
uvx --from git+https://github.com/tawhidUnhappy/mangaEasy.git mangaeasy --help
```

---

## Option 2 — Download a frozen release (no Python needed)

The [**Releases page**](https://github.com/tawhidUnhappy/mangaEasy/releases/latest)
ships a self-contained frozen build of the CLI per platform:

| Platform | File | Run |
|---|---|---|
| Windows | `mangaeasy-windows.zip` | unzip → `mangaEasy\mangaeasy.exe --help` |
| macOS (Apple Silicon) | `mangaeasy-macos-arm64.zip` | unzip → `xattr -cr mangaEasy.app` once → `mangaEasy.app/Contents/MacOS/mangaeasy --help` |
| Linux | `mangaeasy-linux.tar.gz` | `tar xzf` → `mangaEasy/mangaeasy --help` |

No system Python is required — the build bundles it. The archives are unsigned
(free software, no paid certificate): on Windows SmartScreen click **More
info → Run anyway**; on macOS run the `xattr -cr` line above once.

---

## Option 3 — From source (contributors)

```bash
git clone https://github.com/tawhidUnhappy/mangaEasy.git
cd mangaEasy
uv sync
uv run mangaeasy --help
```

Or run `./run.sh` (macOS/Linux) / `run.bat` (Windows) from the repo root — it
runs `uv sync` and prints the command list. New to the code? Open
[START_HERE.md](../START_HERE.md).

Build a frozen release yourself with PyInstaller:

```bash
uv sync --group dev
uv run pyinstaller packaging/mangaeasy.spec
# Output: dist/mangaEasy/ (Windows/Linux) or dist/mangaEasy.app/ (macOS)
```

---

## First-run setup

One command (GPU-aware — details in [setup.md](setup.md)):

```bash
mangaeasy setup
```

It vendors the core binaries (ffmpeg/uv/git-lfs), then installs the AI tool
envs + models that fit the machine: Kokoro TTS always; IndexTTS, MAGI v3,
DeepSeek-OCR 2 and Z-Image Turbo when an NVIDIA GPU is present. `--minimal`,
`--all`, `--skip <tool>`, `--dry-run` variants; safe to re-run (resumes).

Prefer picking pieces yourself?

```bash
mangaeasy doctor --json          # ffmpeg/git/GPU/tool status
mangaeasy bootstrap-tools        # one-time ~100 MB: ffmpeg/ffprobe/uv/git-lfs
mangaeasy install-tool kokoro-82m   # lightweight CPU TTS
mangaeasy install-tool index-tts    # optional: GPU voice cloning
mangaeasy install-tool magi-v3      # optional: paged-manga panel detection
```

Everything mangaEasy writes — installed AI tools, models, settings, logs, and
(by default) your projects — lives under one data folder, so deleting it leaves
no trace. GPU acceleration (NVIDIA CUDA / Apple Silicon) is detected
automatically, with CPU fallback everywhere. Override the data root with the
`MANGAEASY_ROOT` environment variable.

### Where your data lives

| Platform | Data folder (when `MANGAEASY_ROOT` is unset) |
|---|---|
| Windows (frozen) | next to the exe |
| macOS | `~/Library/Application Support/mangaEasy` |
| Linux | `~/.local/share/mangaEasy` (or `$XDG_DATA_HOME/mangaEasy`) |
| Dev checkout | the repo root |

---

## Updating

- **uv install**: `uv tool upgrade mangaeasy`.
- **Frozen release**: download the newer archive and replace the old one; your
  data folder is separate, so installed tools/projects carry over.
- **Source**: `git pull && uv sync`.
