# Installing the AI Tools

The heavy AI models live in **isolated `uv` environments** so their
torch/transformers stacks never conflict with the main `mangaeasy` install.
`mangaeasy install-tool` provisions them for you from GitHub / Hugging Face.

```bash
mangaeasy install-tool              # list available tools
mangaeasy install-tool index-tts    # install one
mangaeasy doctor                    # check what's installed
```

All tools go into the managed folder `~/.mangaeasy/tools` (override with
`MANGAEASY_TOOLS_DIR`), so a globally-installed `mangaeasy` finds them from any
working directory. Re-running an install updates an existing clone in place —
that's also how you pull the **latest version** later.

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

## index-tts (IndexTTS-2)

What the installer does:

1. `git clone https://github.com/index-tts/index-tts` (+ `git lfs pull`)
2. `uv sync --all-extras` — builds its own env with its own torch/CUDA stack
3. Downloads the `IndexTeam/IndexTTS-2` weights into `checkpoints/` via the
   Hugging Face CLI (`uvx --from "huggingface-hub[cli,hf_xet]" hf download …`)
4. Verifies `indextts.infer_v2` imports inside that env

Requirements: git, git-lfs, uv. An NVIDIA GPU (CUDA 12.8+) makes synthesis much
faster, but CPU-only machines work too — mangaEasy loads the model without the
CUDA kernels automatically. The model download is large (several GB).

Used by: `mangaeasy video` (the default engine when an NVIDIA GPU is present),
`mangaeasy video-audio-indextts`, `mangaeasy index-tts`. Voice cloning needs a
speaker reference WAV: `config.system.json → tts.speaker_wav`
(default `vocal/manga[vocal2].wav`) or `--speaker-wav`.

## magi-v3 (MAGI v3 panel detection)

MAGI v3 is not a pip package — it's a Hugging Face model
(`ragavsachdeva/magiv3`) loaded via `transformers` with `trust_remote_code`.
The installer therefore *authors* a small environment instead of cloning:

1. Writes a minimal `pyproject.toml` (torch + transformers + pillow + numpy +
   einops + timm, with the torch build matching your hardware)
2. Copies in `detect_magi.py` — the adapter mangaEasy calls for detection
   (shipped inside the mangaeasy package)
3. `uv sync` and verifies `transformers` imports

The model code and weights download from Hugging Face automatically on the
first detection run. Pass `--clone` if you also want the upstream
`ragavsachdeva/magi` repo checked out for reference.

Used by: panel detection in `mangaeasy cut-page` (and anything calling
`mangaeasy.panels.ai`).

## kokoro-82m (Kokoro, the default TTS)

Kokoro ([hexgrad/kokoro](https://github.com/hexgrad/kokoro)) is pip-installable,
so the installer authors a small environment:

1. Writes a minimal `pyproject.toml` (`kokoro` + torch matching your hardware,
   soundfile, numpy)
2. `uv sync` and verifies `kokoro` imports

The [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) model
weights download from Hugging Face automatically on the first run. The default
voice is **`af_heart`** (change with `--voice` on `mangaeasy video` /
`video-audio`). On Windows, install eSpeak NG for the widest language support —
mangaEasy adds it to the tool's PATH automatically when present.

Used by: `mangaeasy video` (the default engine on machines without an NVIDIA
GPU, or when IndexTTS isn't set up), `mangaeasy video-audio`.

## Manual installs / custom locations

`install-tool` is a convenience, not a requirement. Any folder that contains a
`.venv` (or is a uv project) with the right name works:

- managed: `~/.mangaeasy/tools/<name>`
- sibling: `./<name>` next to where you run `mangaeasy`
- explicit: `KOKORO_ROOT`, `INDEX_TTS_ROOT`, `MAGI_V3_ROOT`

Check resolution any time with `mangaeasy tools` or `mangaeasy doctor`.
