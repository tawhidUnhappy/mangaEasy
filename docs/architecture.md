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

Kokoro, IndexTTS, F5-TTS, and MAGI can each keep their own Python, CUDA, Torch,
and Transformers dependencies as sibling `uv` projects:

```text
workspace/
  mangaEasy/
  kokoro-82m/
  index-tts/
  f5-tts/
  magi-v3/
```

This avoids dependency conflicts while still allowing full GPU acceleration.

## GPU strategy

Audio:

- `mangaeasy video-audio` calls `kokoro-82m` with that tool's own Python.
- `--device auto` uses CUDA when available.
- `--device cuda` fails fast if CUDA is not visible.

Video:

- `--encoder auto` detects H.264 encoders exposed by FFmpeg.
- Preference order: `h264_nvenc`, `h264_amf`, `h264_qsv`,
  `h264_videotoolbox`, `libx264`.

## Package data

Web templates and static files are included under `mangaeasy/assets`. If a
project folder has its own `templates/` or `static/`, those local files override
the packaged defaults.
