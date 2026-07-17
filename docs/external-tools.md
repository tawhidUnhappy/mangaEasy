# External Tools

Heavy model tools run in their own isolated `uv` environments, each with its
own `.venv`, Python, and CUDA/Torch stack. The easiest way to provision them is
`mediaconductor install-tool <name>` — see [install-tools.md](install-tools.md).

`READY.json` records a completed local installation, and `doctor` rechecks the
interpreter, adapters, model directories, and required model payloads without
contacting the network. ACE-Step, Demucs, WhisperX, IndexTTS, DeepSeek-OCR 2,
and Z-Image use explicit model revisions. Kokoro, MAGI, and the optional generic
Faster Whisper integration resolve model weights on first use and are therefore
not bit-reproducible yet.

## Lookup

Run:

```bash
mediaconductor tools
```

The resolver checks, in order:

1. The tool's environment variable:
   - `ACESTEP_ROOT` (or `ACE_STEP_ROOT`)
   - `DEMUCS_ROOT`
   - `WHISPERX_ROOT`
   - `KOKORO_ROOT`
   - `INDEX_TTS_ROOT` (or legacy `INDEX_TTS_DIR`)
   - `MAGI_V3_ROOT` (or legacy `MAGI_V3_DIR`)
   - `DEEPSEEK_OCR2_ROOT` (or `DEEPSEEK_OCR2_DIR`)
   - `Z_IMAGE_TURBO_ROOT` (or `Z_IMAGE_TURBO_DIR`)
   - `GEMMA_4_ROOT` (or `GEMMA_ROOT`)
2. The managed tools dir: `<install folder>/.mangaeasy/tools/<name>`
   (override with `MEDIACONDUCTOR_TOOLS_DIR`)
If a tool has `.venv/Scripts/python.exe` (Windows) or `.venv/bin/python` (Unix),
MediaConductor uses it directly. Otherwise it falls back to `uv run --project`.

## ACE-Step 1.5 (song generation)

`mediaconductor install-tool ace-step` checks out the official ACE-Step 1.5
source at a pinned commit, uses its committed uv lock in a dedicated Python
3.12 environment, and downloads `ACE-Step/Ace-Step1.5` from Hugging Face at an
immutable revision. Readiness verifies the DiT, language model, Qwen embedding,
VAE, and configuration payloads. The adapter writes one requested audio file
and records the prompt, lyrics, duration, seed, and output path.

```bash
mediaconductor ace-step --prompt "cinematic synth pop, clear lead vocal" \
  --lyrics-file lyrics.txt --output song.wav --duration 180 --seed 7
```

ACE-Step is GPU-oriented and can be impractically slow on CPU. Its environment
remains separate from Demucs, WhisperX, Z-Image, and the core CLI so their
Torch/CUDA requirements cannot conflict.

## Demucs (song vocal separation)

`mediaconductor install-tool demucs` installs the maintained fork at a pinned
commit in an isolated uv environment and downloads a pinned
`adefossez/HTDemucs-ft` Hugging Face snapshot. The shipped adapter validates
the snapshot, forces Hub offline mode, and maps only its allow-listed YAML and
safetensors files into Demucs' loader. A song run therefore cannot silently
refresh a model, fall back to an upstream download, or select an unprovisioned
"fast" model. It produces exactly `vocals.wav` and `accompaniment.wav`.

## WhisperX (song lyric timing)

`mediaconductor install-tool whisperx` creates a separate uv environment and
downloads two immutable Hugging Face snapshots: Systran's faster-whisper
Large-v3 transcription model and Facebook's Wav2Vec2 Base 960h English forced
aligner. The adapter loads both from local paths with Hub/Transformers offline
mode enabled; setup also provisions NLTK's sentence data into the managed
cache. Supplied lyrics remain canonical—the models provide timing evidence.

The bundled offline aligner supports English. A language-specific Wav2Vec2 CTC
extension is required before using another language; MediaConductor currently
rejects that manifest rather than silently fetching a floating model during a
render.

## TTS engine selection

`mediaconductor video` picks the engine automatically (`--tts auto`, the default):

- **IndexTTS** when an NVIDIA GPU is present, the `index-tts` env is installed
  with checkpoints, and the speaker reference WAV exists — best quality.
- **Kokoro** otherwise — light and fast enough on any CPU.

Force a specific engine with `--tts indextts` or `--tts kokoro`.

## Kokoro

Used by:

```bash
mediaconductor video          # default engine on machines without an NVIDIA GPU
mediaconductor video-audio
```

Install with `mediaconductor install-tool kokoro-82m`. MediaConductor sends a manifest
to `mediaconductor.video_pipeline.kokoro_batch_worker` and executes it inside the
Kokoro environment. Its model is a legacy first-use download from the current
Hub default revision, not an immutable installer snapshot.

## IndexTTS

Used by:

```bash
mediaconductor video          # default engine on NVIDIA GPU machines
mediaconductor video-audio-indextts
mediaconductor index-tts
```

Install with `mediaconductor install-tool index-tts`. IndexTTS stays isolated
because its dependency stack is large and can conflict with other tools. Its
source checkout and Hugging Face checkpoint are both immutable revisions, and
readiness verifies every required checkpoint payload.

## MAGI v3 (panel detection)

Used by panel detection when `MEDIACONDUCTOR_EXTERNAL_MAGI` is not `0`.

The external MAGI environment must expose:

```text
magi-v3/detect_magi.py
```

`mediaconductor install-tool magi-v3` creates this automatically — the adapter ships
inside the mediaconductor package (`mediaconductor/assets/tools/detect_magi.py`) and is
copied into the tool folder. The `ragavsachdeva/magiv3` model code/weights
download from the current Hugging Face default revision on the first run.

Set `MEDIACONDUCTOR_EXTERNAL_MAGI=0` only when the main package env has the `ml`
extra installed and you intentionally want in-process detection.

## DeepSeek-OCR 2

Used by:

```bash
mediaconductor deepseek-ocr2 --project-root content
mediaconductor deepseek-ocr2 --project-root content --item-range 01-24 --device cuda
```

Install with `mediaconductor install-tool deepseek-ocr2`. The installer creates an
isolated uv environment and downloads the `deepseek-ai/DeepSeek-OCR-2` model
from Hugging Face into `deepseek-ocr2/model`. The command scans narration JSON
files, finds each panel image, and adds an `ocr` field to every entry that does
not already have one. Both the optional source clone and model snapshot are
commit-pinned and locally health-checked. Use `--force` to regenerate existing
OCR, or pass
`--prompt "<image>\n<|grounding|>Convert the document to markdown."` for
document-style markdown OCR.

## Z-Image Turbo (image generation)

Text-to-image generation with Alibaba Tongyi's
[Z-Image-Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo)
(Apache-2.0, 6B DiT + Qwen3-4B text encoder) — thumbnails, backgrounds,
channel art. Used by:

```bash
mediaconductor zimage --prompt "glossy anime scene, two characters facing off..." \
    --output thumb.png --width 1280 --height 720
mediaconductor zimage --prompt-file prompt.txt --output art.png --count 4 --seed 7
```

Install with `mediaconductor install-tool z-image-turbo` (~33 GB model download
into `z-image-turbo/model`). The `generate_zimage.py` adapter ships inside
the package and is copied into the tool folder. The installer locks the model
to an immutable Hub commit and `doctor` verifies its complete required payload.

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

On success the command prints `MEDIACONDUCTOR_RESULT {"outputs": [...]}` with
every generated file.

## Gemma 4 (local LLM, text + vision)

Google's [Gemma 4 E4B instruct](https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF)
(Apache-2.0) is the local LLM behind the assist commands — the model that lets
a small or text-only driver agent still get vision-grounded results:

```bash
mediaconductor install-tool gemma-4
mediaconductor llm --prompt "Summarize this panel" --image panels/ch01_001.jpg
mediaconductor crop-qa --project-root library/example --items 01
mediaconductor characters --project-root library/example --auto-draft
mediaconductor narrate-auto --project-root library/example --items 01
```

The install is different from the Torch-based tools: the runtime is a **pinned
llama.cpp release binary** (`llama-server`, extracted into `gemma-4/llama/`),
and the weights are a revision-pinned GGUF snapshot in `gemma-4/model/`
(Q4_0 main model ~5.4 GB + Q8_0 vision projector ~0.6 GB; the BF16/Q8/mtp
variants in the upstream repo are deliberately not downloaded). The tool env
itself only carries Pillow (the adapter downscales panel images before
base64-ing them into vision requests).

Hardware handling: the Windows/Linux GPU builds use **Vulkan** (works on
NVIDIA without the ~640 MB CUDA runtime download; `-ngl 99` offload is always
requested and harmlessly ignored by CPU builds), macOS arm64 uses Metal, and
every platform falls back to a CPU build that runs fine — just slower. A
custom llama.cpp can be supplied with `MEDIACONDUCTOR_LLAMA_SERVER=<path to
llama-server>`.

Calling conventions: one `llm`/assist invocation starts one `llama-server`,
waits for `/health`, sends every request of the batch through
`/v1/chat/completions` (JSON-schema-constrained when requested), and always
tears the server down. Model load takes seconds on GPU and up to a minute or
two on CPU, so batched forms (`--batch-manifest`, the assist commands) are
strongly preferred over per-request invocations.
