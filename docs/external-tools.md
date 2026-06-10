# External Tools

The package works best when heavy model tools are installed as sibling `uv`
projects, each with its own `.venv`, Python, and CUDA/Torch stack.

## Lookup

Run:

```bash
mangaeasy tools
```

The resolver checks, in order:

- `KOKORO_ROOT`, then a sibling `kokoro-82m`
- `INDEX_TTS_ROOT` (or legacy `INDEX_TTS_DIR`), then a sibling `index-tts`
- `F5_TTS_ROOT`, then a sibling `f5-tts`
- `MAGI_V3_ROOT` (or legacy `MAGI_V3_DIR`), then a sibling `magi-v3`

If a tool has `.venv/Scripts/python.exe` (Windows) or `.venv/bin/python` (Unix),
`mangaeasy` uses it directly. Otherwise it falls back to `uv run --project`.

## Kokoro (default TTS)

Used by:

```bash
mangaeasy video-audio
mangaeasy video
```

Install Kokoro as a separate `uv` project named `kokoro-82m` next to your
working folder. `mangaeasy` sends a manifest to
`mangaeasy.video_pipeline.kokoro_batch_worker` and executes it inside the Kokoro
environment.

## IndexTTS

Used by:

```bash
mangaeasy video-audio-indextts
mangaeasy index-tts
```

IndexTTS stays isolated because its dependency stack is large and can conflict
with other tools.

## F5-TTS

Used by:

```bash
mangaeasy video-audio-f5tts
mangaeasy f5-tts
```

Install F5-TTS as a sibling `f5-tts` project (or set `F5_TTS_ROOT`).

## MAGI v3 (panel detection)

Used by panel detection when `MANGAEASY_EXTERNAL_MAGI` is not `0`.

The external MAGI project should expose:

```text
magi-v3/detect_magi.py
```

Set `MANGAEASY_EXTERNAL_MAGI=0` only when the main package env has the `ml`
extra installed and you intentionally want in-process detection.
