# mangaeasy/tools — external AI tool environments + vendored binaries

Heavy AI models (panel detection, TTS, OCR, image generation) each run in their
**own isolated `uv` environment** under `<data>/.mangaeasy/tools/<tool>/`, so
their CUDA/Torch/Transformers versions can't conflict with the main package or
each other. This package installs them, resolves them, and builds the
subprocess environment they run in.

See [docs/external-tools.md](../../docs/external-tools.md) and
[docs/install-tools.md](../../docs/install-tools.md) for install mechanics; this
README is the code map.

## Files

| File | Command | Role |
|---|---|---|
| [`setup.py`](setup.py) | `setup` | one-command provisioning: core binaries + GPU-aware tool selection (`plan_tools`) + installs + doctor summary; idempotent/resumable ([docs/setup.md](../../docs/setup.md)) |
| [`install.py`](install.py) | `install-tool`, `doctor` | `TOOLS` registry (`ToolSpec` per tool) + installer; copies `adapter`/`extra_adapters` scripts from `assets/tools/` into each env |
| [`external.py`](external.py) | `tools`, `where` | resolve an installed tool dir (`resolve_tool_dir`), build its subprocess env (`tool_env`), interpreter path (`python_command`), device/root resolution |
| [`vendored.py`](vendored.py) | `bootstrap-tools` | vendor ffmpeg/uv/git-lfs into the install so users need nothing on PATH; `ensure_vendored_path()` runs at CLI startup |
| [`hardware.py`](hardware.py) | — | GPU/VRAM detection used to choose engines and quantization |
| [`index_tts.py`](index_tts.py) | `index-tts` | run IndexTTS in its env |
| [`deepseek_ocr2.py`](deepseek_ocr2.py) | `deepseek-ocr2` | run DeepSeek-OCR 2, write `ocr` fields into narration JSON |
| [`zimage.py`](zimage.py) | `zimage` | Z-Image Turbo text-to-image (thumbnails) |

Tool adapters (the code that actually runs *inside* each env, with no mangaeasy
imports) live in [`../assets/tools/`](../assets/tools/): `detect_magi.py`,
`batch_detect_magi.py` (used by `page-split`), `generate_zimage.py`.

## Gotchas

- **`tool_env()` force-pins** `HF_HOME`/`HF_HUB_CACHE`/`TRANSFORMERS_CACHE`/
  `TORCH_HOME`/`UV_CACHE_DIR` under `<data>/.mangaeasy/` (an *override*, not
  `setdefault`) so a machine-global `HF_HOME` can't scatter multi-GB downloads
  outside the install. `MANGAEASY_SHARE_CACHES=1` opts back into a shared cache.
  Don't turn these back into `setdefault` without that opt-out.
- **Adding a file an env needs at runtime**: put it in `assets/tools/` and add
  it to that tool's `ToolSpec.adapter` or `extra_adapters` so `install-tool`
  copies it in. (This is how `page-split`'s batch detector ships.)
- Model-specific facts that must not be "optimized" away (Z-Image guidance_scale
  0.0, bf16/fp32 only, MAGI transformers 4.48.3 + eager attn) live in
  [CLAUDE.md](../../CLAUDE.md) and the project memory.

## Tests

[tests/test_tool_env.py](../../tests/test_tool_env.py).
