# External Tools

Heavy model tools run in their own isolated `uv` environments, each with its
own `.venv`, Python, and CUDA/Torch stack. The easiest way to provision them is
`mangaeasy install-tool <name>` — see [install-tools.md](install-tools.md).

## Lookup

Run:

```bash
mangaeasy tools
```

The resolver checks, in order:

1. The tool's environment variable:
   - `KOKORO_ROOT`
   - `INDEX_TTS_ROOT` (or legacy `INDEX_TTS_DIR`)
   - `MAGI_V3_ROOT` (or legacy `MAGI_V3_DIR`)
2. The managed tools dir: `~/.mangaeasy/tools/<name>`
   (override with `MANGAEASY_TOOLS_DIR`)
3. A folder named `<name>` in the current directory, its parent, or next to the
   installed package

If a tool has `.venv/Scripts/python.exe` (Windows) or `.venv/bin/python` (Unix),
`mangaeasy` uses it directly. Otherwise it falls back to `uv run --project`.

## TTS engine selection

`mangaeasy video` picks the engine automatically (`--tts auto`):

- **IndexTTS** when an NVIDIA GPU is present, the `index-tts` env is installed
  with checkpoints, and the speaker reference WAV exists — best quality.
- **Kokoro** otherwise — light and fast enough on any CPU.

Force a specific engine with `--tts indextts` or `--tts kokoro`.

## Kokoro

Used by:

```bash
mangaeasy video          # default engine on machines without an NVIDIA GPU
mangaeasy video-audio
```

Install with `mangaeasy install-tool kokoro-82m`. `mangaeasy` sends a manifest
to `mangaeasy.video_pipeline.kokoro_batch_worker` and executes it inside the
Kokoro environment.

## IndexTTS

Used by:

```bash
mangaeasy video          # default engine on NVIDIA GPU machines
mangaeasy video-audio-indextts
mangaeasy index-tts
```

Install with `mangaeasy install-tool index-tts`. IndexTTS stays isolated
because its dependency stack is large and can conflict with other tools.

## MAGI v3 (panel detection)

Used by panel detection when `MANGAEASY_EXTERNAL_MAGI` is not `0`.

The external MAGI environment must expose:

```text
magi-v3/detect_magi.py
```

`mangaeasy install-tool magi-v3` creates this automatically — the adapter ships
inside the mangaeasy package (`mangaeasy/assets/tools/detect_magi.py`) and is
copied into the tool folder. The `ragavsachdeva/magiv3` model code/weights
download from Hugging Face on the first run.

Set `MANGAEASY_EXTERNAL_MAGI=0` only when the main package env has the `ml`
extra installed and you intentionally want in-process detection.
