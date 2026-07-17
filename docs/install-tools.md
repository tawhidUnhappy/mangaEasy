# Installing the AI Tools

The heavy AI models live in **isolated `uv` environments** so their
torch/transformers stacks never conflict with the main `mediaconductor` install.
`mediaconductor install-tool` provisions them from GitHub and Hugging Face.

```bash
mediaconductor install-tool               # list available tools
mediaconductor install-tool index-tts     # install one
mediaconductor install-tool deepseek-ocr2 # DeepSeek-OCR 2 panel/document OCR
mediaconductor doctor                     # check what's installed
```

All tools go into the managed folder `<install folder>/.mangaeasy/tools` (override with
`MEDIACONDUCTOR_TOOLS_DIR`), so a globally-installed `mediaconductor` finds them from
any working directory. Re-running an install resumes interrupted downloads.
Installer-managed snapshots remain on their immutable manifest revisions.
The lightweight Hugging Face downloader is also provisioned through `uvx` at
the tested `huggingface-hub==1.23.0` release.

**Works with or without a GPU.** The installer auto-detects your hardware:

- NVIDIA GPU on Windows/Linux → CUDA 12.8 torch builds
- everything else (macOS, AMD, Intel, plain CPU) → standard CPU builds
  (on Linux it uses PyTorch's lean CPU index to avoid gigabytes of CUDA libs)

Common flags:

| Flag | Effect |
|------|--------|
| `--ref <branch/tag/commit>` | Check out a specific version instead of the default branch. |
| `--cpu` / `--cuda` | Force CPU or CUDA torch builds instead of auto-detecting. |
| `--skip-model` | Skip the large model-weight download. |
| `--dir <path>` | Install somewhere other than the managed folder. |

### Model revision and readiness policy

`doctor` reports `ready` only when the isolated interpreter, shipped adapters,
`READY.json`, and installer-managed model snapshots are still present. It
checks every required model file and rejects empty payloads or inconsistent
markers without contacting the network.

- Revision-pinned installer snapshots: ACE-Step 1.5, Demucs, both WhisperX
  snapshots, IndexTTS 2, DeepSeek-OCR 2, and Z-Image Turbo.
- Downloaded on first use without an installer-recorded immutable revision:
  Kokoro 82M, MAGI v3, and optional Faster Whisper models.

## ace-step (ACE-Step 1.5 song generation)

The installer shallow-clones the official
[`ace-step/ACE-Step-1.5`](https://github.com/ace-step/ACE-Step-1.5) source at
commit `dce621408bee8c31b4fcf4811682eb9359e1bc94`, syncs its committed uv lock in
an isolated Python 3.12 environment, copies MediaConductor's adapter, and
downloads `ACE-Step/Ace-Step1.5` from Hugging Face at immutable revision
`19671f406d603126926c1b7e2adc169acbcade22`. Readiness verifies the required
DiT, 5 Hz language model, Qwen embedding model, VAE, and configuration files.

```bash
mediaconductor install-tool ace-step
mediaconductor ace-step --prompt "cinematic synth pop, clear lead vocal" \
  --lyrics-file lyrics.txt --output song.wav --duration 180 --seed 7
```

ACE-Step is GPU-oriented. It owns its upstream Torch stack and never shares a
venv with Demucs, WhisperX, Z-Image, or the core application.

## index-tts (IndexTTS-2)

What the installer does:

1. Shallow-clones `https://github.com/index-tts/index-tts` at commit
   `13495845e3028f0bb6ca1462ad22aa0e76349e40`, with Git LFS model smudging
   disabled
2. `uv sync` excluding the `deepspeed`, `accel`, and `webui` extras — builds
   its own env with its own torch/CUDA stack. DeepSpeed and flash-attn
   (`accel`) are training/serving accelerators whose native builds fail on
   most machines (flash-attn needs torch at build time and has no Windows
   wheel), and inference never imports them; `webui` is gradio-only.
3. Downloads `IndexTeam/IndexTTS-2` at immutable revision
   `740dcaff396282ffb241903d150ac011cd4b1ede` into `checkpoints/` via the
   Hugging Face CLI and verifies its required inference payload.
4. Verifies `indextts.infer_v2` imports inside that env

Requirements: git and uv. An NVIDIA GPU (CUDA 12.8+) makes synthesis much
faster, but CPU-only machines work too — MediaConductor loads the model without the
CUDA kernels automatically. The model download is large (several GB).

Used by: `mediaconductor video` (the default engine when an NVIDIA GPU is present),
`mediaconductor video-audio-indextts`, `mediaconductor index-tts`. Voice cloning needs a
speaker reference WAV: `config.system.json → tts.speaker_wav`
(`tts.speaker_wav`) or `--speaker-wav`; no voice sample ships with the repository.

## magi-v3 (MAGI v3 panel detection)

MAGI v3 is not a pip package — it's a Hugging Face model
(`ragavsachdeva/magiv3`) loaded via `transformers` with `trust_remote_code`.
The installer therefore *authors* a small environment instead of cloning:

1. Writes a minimal `pyproject.toml` (torch + transformers + pillow + numpy +
   einops + timm, with the torch build matching your hardware)
2. Copies in `detect_magi.py` — the adapter MediaConductor calls for detection
   (shipped inside the mediaconductor package)
3. `uv sync` and verifies `transformers` imports

The model code and weights download from the current Hugging Face default
revision on the first detection run; this legacy first-use download is not
immutable. Pass `--clone` if you also want the upstream
`ragavsachdeva/magi` repo checked out for reference.

Used by: panel detection in `mediaconductor page-split` (and anything calling
`mediaconductor.panels.ai`).

## deepseek-ocr2 (DeepSeek-OCR 2 panel/document OCR)

DeepSeek-OCR 2 is installed as a managed Hugging Face environment. Its optional
official source clone is locked to commit
`2f3699ebbb96fa8af32212e8c170f2cc28730fad`; the installer writes an isolated
`pyproject.toml`, installs torch and transformers for the selected CPU/CUDA
target, and downloads `deepseek-ai/DeepSeek-OCR-2` revision
`aaa02f3811945a91062062994c5c4a3f4c0af2b0` into `deepseek-ocr2/model`.

```bash
mediaconductor install-tool deepseek-ocr2
mediaconductor deepseek-ocr2 --project-root content --item-range 01-24
```

The run command scans `narration.json` and `narration_*.json` files, resolves
each entry's panel image, calls DeepSeek-OCR 2 with a plain OCR prompt by
default, and writes:

```json
{ "image": "panel_001.png", "narration": "...", "ocr": "..." }
```

Existing `ocr` values are preserved; pass `--force` to regenerate them. Use
`--device cuda` to fail fast if CUDA is not available, or leave the default
`--device auto`.

## kokoro-82m (Kokoro, the default TTS)

Kokoro ([hexgrad/kokoro](https://github.com/hexgrad/kokoro)) is pip-installable,
so the installer authors a small environment:

1. Writes a minimal `pyproject.toml` (`kokoro` + torch matching your hardware,
   soundfile, numpy)
2. `uv sync` and verifies `kokoro` imports

The [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) model
weights download from Hugging Face automatically on the first run. That legacy
first-use revision is not pinned by the installer. The default voice is
**`af_heart`** (change with `--voice` on `mediaconductor video` /
`video-audio`). On Windows, install eSpeak NG for the widest language support —
MediaConductor adds it to the tool's PATH automatically when present.

Used by: `mediaconductor video` (the default engine on machines without an NVIDIA
GPU, or when IndexTTS isn't set up), `mediaconductor video-audio`.

## z-image-turbo (Z-Image Turbo image generation)

Z-Image Turbo ([Tongyi-MAI/Z-Image-Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo),
Apache-2.0) is installed as a managed environment: the installer writes an
isolated `pyproject.toml` (torch matching your hardware, diffusers ≥ 0.36,
transformers ≥ 4.51, accelerate, bitsandbytes on Windows/Linux), copies the
`generate_zimage.py` adapter in, and downloads the **~33 GB** model into
`z-image-turbo/model` at immutable revision
`f332072aa78be7aecdf3ee76d5c247082da564a6`. `--skip-model` deliberately leaves
the tool not ready; reinstall without that flag before production inference.

```bash
mediaconductor install-tool z-image-turbo
mediaconductor zimage --prompt "..." --output out.png --width 1280 --height 720
```

It runs on 8–16 GB NVIDIA GPUs via automatic NF4 quantization (this is why
bitsandbytes is a dependency), full bf16 on 16 GB+ GPUs and Apple Silicon,
and fp32 on CPU (slow). See `docs/external-tools.md` for the strategy table
and calling conventions.

## demucs (offline vocal separation)

The installer creates a dedicated uv environment for the maintained Demucs
fork, copies `separate_demucs.py`, and downloads the exact pinned revision of
`adefossez/HTDemucs-ft` into `models/htdemucs-ft/`. At runtime the adapter puts
Hugging Face in offline mode and serves only the manifest and safetensors files
from that local snapshot; it never resolves a floating Hub model.

```bash
mediaconductor install-tool demucs
mediaconductor demucs --audio song.wav --output-dir stems
```

The output contract is always `stems/vocals.wav` and
`stems/accompaniment.wav`. Only the provisioned fine-tuned quality model is
exposed. If installation used `--skip-model`, Demucs intentionally fails with
a reinstall instruction instead of downloading weights during a production
run.

## whisperx (offline English lyric timing)

The isolated WhisperX setup downloads pinned local snapshots for
`Systran/faster-whisper-large-v3` and `facebook/wav2vec2-base-960h`, plus the
small NLTK sentence resource used by alignment. Normal English transcription
and forced alignment therefore perform no runtime model lookup. Canonical
lyrics are compared with the timed vocal evidence and always supply the final
displayed spelling and line breaks.

```bash
mediaconductor install-tool whisperx
mediaconductor whisperx --audio stems/vocals.wav --lyrics-file lyrics.txt --output-dir alignment --language en
```

Language-specific forced aligners are not interchangeable. The current
production contract rejects non-English manifests until an appropriate pinned
Wav2Vec2 CTC extension is added.

## gemma-4 (Gemma 4 E4B local LLM, text + vision)

Google's Gemma 4 E4B instruct model (Apache-2.0) powering `llm`, `crop-qa`,
`characters --auto-draft`, `narrate-auto`, and the review gates of
`manga-auto`:

```bash
mediaconductor install-tool gemma-4
mediaconductor llm --prompt "ping"
```

The installer:

1. Writes a tiny managed env (Pillow only — no Torch; the heavy lifting is
   native llama.cpp) and copies in the `run_gemma.py` adapter.
2. Downloads a revision-pinned GGUF snapshot from
   `ggml-org/gemma-4-E4B-it-GGUF` into `gemma-4/model/` — only the Q4_0
   weights (~5.4 GB) and the Q8_0 vision projector (~0.6 GB), never the
   BF16/Q8/mtp variants.
3. Downloads the pinned llama.cpp release binaries into `gemma-4/llama/`
   (Vulkan build with a GPU, CPU build otherwise; macOS uses Metal).

Runs on any machine: ~6 GB RAM at 4-bit on CPU, faster with GPU offload.
Point `MEDIACONDUCTOR_LLAMA_SERVER` at your own `llama-server` to use a
custom llama.cpp build. See [docs/local-llm.md](local-llm.md) for what the
assist commands do with it.

## Manual installs / custom locations

`install-tool` is a convenience, not a requirement. Any folder that contains a
`.venv` (or is a uv project) with the right name works:

- managed: `<install folder>/.mangaeasy/tools/<name>`
- explicit: `ACESTEP_ROOT`, `DEMUCS_ROOT`, `WHISPERX_ROOT`, `KOKORO_ROOT`,
  `INDEX_TTS_ROOT`, `MAGI_V3_ROOT`, `DEEPSEEK_OCR2_ROOT`, `Z_IMAGE_TURBO_ROOT`,
  `GEMMA_4_ROOT`

Check resolution any time with `mediaconductor tools` or `mediaconductor doctor`.
