# Architecture

`mangaeasy` is intentionally split into a small main package and optional
external AI tools, all driven through one CLI.

## One command

Everything is reachable through the single `mangaeasy` entry point
(`mangaeasy.cli:main`). It is a thin dispatcher: it maps a subcommand name to a
module and calls that module's `main()`, importing the module **lazily** so
`mangaeasy --help` never pulls in heavy optional dependencies.

```text
mangaeasy <command> [args...]  ->  mangaeasy.<area>.<module>:main()
```

## Main package

The main package contains:

- General item-based video pipeline in `mangaeasy.video_pipeline`
- Original manga/chapter utilities in `mangaeasy.download`, `mangaeasy.web`,
  `mangaeasy.video`, `mangaeasy.images`, and related modules
- External tool lookup/wrappers in `mangaeasy.tools`
- Packaged Flask templates/static files in `mangaeasy.assets`

The default project root is the current working directory. Set
`MANGAEASY_PROJECT_ROOT` to run commands against another folder.

## External tools

Kokoro, IndexTTS, MAGI, DeepSeek-OCR 2, and Z-Image Turbo can each keep their
own Python, CUDA, Torch, and Transformers dependencies as isolated `uv`
projects:

```text
<install folder>/.mangaeasy/tools/
  kokoro-82m/
  index-tts/
  magi-v3/
  deepseek-ocr2/
  z-image-turbo/
```

This avoids dependency conflicts while still allowing full GPU acceleration.

## GPU strategy

No GPU is required anywhere — every stage has a CPU path.

Tool installs (`mangaeasy install-tool`):

- Auto-detects hardware: CUDA torch builds only on Windows/Linux with an
  NVIDIA GPU; standard CPU builds everywhere else (macOS, AMD, plain CPU).
- Force a choice with `--cuda` or `--cpu`.

OCR:

- `mangaeasy deepseek-ocr2` runs inside the isolated DeepSeek-OCR 2 environment and
  writes an `ocr` field into narration JSON entries.
- `--device auto` uses CUDA when the tool env can see it, otherwise CPU.

Audio:

- `mangaeasy video --tts auto` (the default) picks IndexTTS when an NVIDIA GPU
  and the installed `index-tts` env are available, otherwise Kokoro.
- `mangaeasy video-audio` calls `kokoro-82m` with that tool's own Python.
- `--device auto` uses CUDA when available, otherwise CPU.
- `--device cuda` fails fast if CUDA is not visible.
- The IndexTTS bridge enables fp16/CUDA kernels only when CUDA is present.

Video:

- `--encoder auto` detects H.264 encoders exposed by FFmpeg.
- Preference order: `h264_nvenc`, `h264_amf`, `h264_qsv`,
  `h264_videotoolbox`, `libx264` (CPU, always available).

## Package data

Web templates and static files are included under `mangaeasy/assets`. If a
project folder has its own `templates/` or `static/`, those local files override
the packaged defaults.
