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
   - `DEEPSEEK_OCR2_ROOT` (or `DEEPSEEK_OCR2_DIR`)
2. The managed tools dir: `<install folder>/.mangaeasy/tools/<name>`
   (override with `MANGAEASY_TOOLS_DIR`)
If a tool has `.venv/Scripts/python.exe` (Windows) or `.venv/bin/python` (Unix),
`mangaeasy` uses it directly. Otherwise it falls back to `uv run --project`.

## TTS engine selection

`mangaeasy video` picks the engine automatically (`--tts auto`, the default):

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

## DeepSeek-OCR 2

Used by:

```bash
mangaeasy deepseek-ocr2 --project-root content
mangaeasy deepseek-ocr2 --project-root content --item-range 01-24 --device cuda
```

Install with `mangaeasy install-tool deepseek-ocr2`. The installer creates an
isolated uv environment and downloads the `deepseek-ai/DeepSeek-OCR-2` model
from Hugging Face into `deepseek-ocr2/model`. The command scans narration JSON
files, finds each panel image, and adds an `ocr` field to every entry that does
not already have one. Use `--force` to regenerate existing OCR, or pass
`--prompt "<image>\n<|grounding|>Convert the document to markdown."` for
document-style markdown OCR.

## Z-Image Turbo (image generation)

Text-to-image generation with Alibaba Tongyi's
[Z-Image-Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo)
(Apache-2.0, 6B DiT + Qwen3-4B text encoder) — thumbnails, backgrounds,
channel art. Used by:

```bash
mangaeasy zimage --prompt "glossy anime scene, two characters facing off..." \
    --output thumb.png --width 1280 --height 720
mangaeasy zimage --prompt-file prompt.txt --output art.png --count 4 --seed 7
```

Install with `mangaeasy install-tool z-image-turbo` (~33 GB model download
into `z-image-turbo/model`). The `generate_zimage.py` adapter ships inside
the mangaeasy package and is copied into the tool folder.

Hardware handling is automatic (`--strategy auto`):

| Hardware | Strategy |
|---|---|
| NVIDIA GPU ≥ 15 GB VRAM | full bf16 on GPU (fastest) |
| NVIDIA GPU 8–12 GB (e.g. RTX 3060) | NF4 4-bit quantization via bitsandbytes (~7 GB VRAM, ~24 s/image) |
| NVIDIA GPU without bitsandbytes | sequential CPU offload (slow but works) |
| Apple Silicon | bf16 on MPS |
| CPU only | fp32 (several minutes per image) |

Facts to respect when calling it programmatically (all enforced by the
adapter): `guidance_scale` is always `0.0` (Turbo has no CFG; negative
prompts are ignored), 8–9 steps is the operating point, **never fp16**
(produces black images — bf16 or fp32 only), sizes are rounded to multiples
of 16. Prompts: English and Chinese, up to 512 tokens; long descriptive
prompts (scene, subject, attire, lighting, composition) give the best
results, and quoted text renders legibly in the image.

On success the command prints `MANGAEASY_RESULT {"outputs": [...]}` with
every generated file.
