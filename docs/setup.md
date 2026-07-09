# One-command setup

From a fresh clone to a fully provisioned install:

```bash
git clone https://github.com/tawhidUnhappy/mangaEasy.git
cd mangaEasy
uv sync
uv run mangaeasy setup
```

(Installed via `uv tool install` or a frozen release instead? Just run
`mangaeasy setup`.)

## What it does, in order

1. **Core binaries** — downloads ffmpeg/ffprobe, uv, and git-lfs (~100 MB)
   into this install's own tools dir. Nothing goes system-wide.
2. **Hardware detection** — checks for an NVIDIA GPU.
3. **AI tool environments** — each into its own isolated `uv` env under
   `.mangaeasy/tools/`, models included:

   | Tool | Installed when | Role |
   |---|---|---|
   | `kokoro-82m` | always | CPU TTS — the universal fallback voice |
   | `index-tts` | NVIDIA GPU | voice-cloning TTS (the recap voice) |
   | `magi-v3` | NVIDIA GPU | panel detection for paged manga |
   | `deepseek-ocr2` | NVIDIA GPU | reading panel text into narration JSON |
   | `z-image-turbo` | NVIDIA GPU | thumbnail/key-art generation (~33 GB) |

4. **Readiness report** — the same data as `mangaeasy doctor --json`,
   plus a `MANGAEASY_RESULT` line with per-tool ok/failed status.

## Variants

```bash
mangaeasy setup --minimal              # core binaries only (fast)
mangaeasy setup --all                  # every tool, GPU or not
mangaeasy setup --skip z-image-turbo   # drop one tool (repeatable)
mangaeasy setup --skip-models          # envs now, model downloads on first use
mangaeasy setup --dry-run              # print the plan, change nothing
mangaeasy setup --cpu | --cuda         # force the torch build choice
```

## Properties worth knowing

- **Idempotent** — re-running updates existing tools and resumes partial
  model downloads; an interrupted run just needs `mangaeasy setup` again.
- **Self-contained** — HF/torch/uv caches are force-pinned under the
  install's `.mangaeasy/` (see [external-tools.md](external-tools.md));
  deleting the folder removes everything.
- **Failure-tolerant** — one tool failing doesn't abort the rest; the
  summary names failures and exit code 1 signals them. Fix with
  `mangaeasy install-tool <name>` or another `setup` run.
- Per-tool installs (choosing refs, forcing dirs) remain available via
  [`mangaeasy install-tool`](install-tools.md).
