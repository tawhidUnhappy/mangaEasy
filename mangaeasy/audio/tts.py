#!/usr/bin/env python3
"""mangaeasy.audio.tts — generate narration audio via IndexTTS2.

IMPORTANT: This module must be run inside the external index-tts uv environment
because IndexTTS2 is only installed there.

Shell usage:
    uv run --directory ../index-tts python mangaeasy/audio/tts.py

The module adds the project root and external index-tts/ to sys.path automatically
so that mangaeasy and indextts are both importable.
"""

import os
import sys
import traceback
from pathlib import Path

# ── Path bootstrap (runs before any other import) ────────────────────────────
_HERE = Path(__file__).resolve()                                   # mangaeasy/audio/tts.py
_PROJECT_ROOT = _HERE.parent.parent.parent                         # project root
_INDEX_TTS_DIR = Path(
    os.environ.get("INDEX_TTS_ROOT")
    or os.environ.get("INDEX_TTS_DIR")
    or (_PROJECT_ROOT.parent / "index-tts")
).resolve()

for _p in (_PROJECT_ROOT, _INDEX_TTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ── Config (sets HF_HOME before any ML import) ───────────────────────────────
from mangaeasy.config import PROJECT_ROOT, HF_CACHE_DIR, load_download_config, load_system_config
from mangaeasy.narration import load_narration
from mangaeasy.narration.clean import clean_text_for_tts
from mangaeasy.paths import audio_dir as _audio_dir, narration_json as _narration_json

# IndexTTS2 stores its own checkpoints under index-tts/checkpoints/.
# We keep HF_HOME pointing at the shared project cache for hub downloads.
CHECKPOINTS_DIR = _INDEX_TTS_DIR / "checkpoints"
INDX_CFG_PATH   = CHECKPOINTS_DIR / "config.yaml"
INDX_MODEL_DIR  = CHECKPOINTS_DIR

# HF_HOME is already set by mangaeasy.config; point also HuggingFace cache
# so any HF downloads by IndexTTS2 land in the same shared folder.
os.environ["HF_HOME"] = str(HF_CACHE_DIR)
os.environ["HUGGINGFACE_HUB_CACHE"] = str(HF_CACHE_DIR)
os.environ.setdefault("HF_HUB_OFFLINE", "0")        # allow first-run download
os.environ.setdefault("TRANSFORMERS_OFFLINE", "0")
os.environ.setdefault("HF_DATASETS_OFFLINE", "0")

# Speaker reference WAV — from config.system.json → tts.speaker_wav
_tts_cfg         = load_system_config().get("tts", {})
SPEAKER_WAV      = PROJECT_ROOT / _tts_cfg.get("speaker_wav", "vocal/manga[vocal]2.wav")
USE_RAW_NARRATION = bool(_tts_cfg.get("use_raw_narration", False))

# ── Import IndexTTS2 ──────────────────────────────────────────────────────────
import torch

try:
    from indextts.infer_v2 import IndexTTS2
except Exception as exc:
    print(f"[FATAL] Could not import IndexTTS2: {exc}")
    print("  Make sure you are running from the external index-tts uv environment:")
    print("    mangaeasy index-tts")
    sys.exit(1)


def generate_and_save_wav(tts_model: IndexTTS2, text: str, out_path: Path) -> None:
    try:
        tts_model.infer(
            spk_audio_prompt=str(SPEAKER_WAV),
            text=text,
            output_path=str(out_path),
            verbose=False,
        )
    except Exception as exc:
        print(f"[ERROR] Failed to generate {out_path.name}: {exc}")
        traceback.print_exc()


# ── Main ─────────────────────────────────────────────────────────────────────

def generate_missing_audio() -> None:
    dl_cfg = load_download_config()
    chapter = int(dl_cfg["chapter"])
    name = str(dl_cfg["name"])

    audio_dir = _audio_dir(name, chapter)
    narration_json = _narration_json(name, chapter)
    audio_dir.mkdir(parents=True, exist_ok=True)

    print("[INFO] Starting IndexTTS2 audio generation...")

    if not SPEAKER_WAV.is_file():
        print(f"[FATAL] Speaker reference missing: {SPEAKER_WAV}")
        sys.exit(1)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[INFO] Using device: {device}")

    try:
        print("[INFO] Loading IndexTTS2 model...")
        tts = IndexTTS2(
            cfg_path=str(INDX_CFG_PATH),
            model_dir=str(INDX_MODEL_DIR),
            use_fp16=True,
            use_cuda_kernel=True,
            use_deepspeed=False,
        )
        print("[INFO] IndexTTS2 loaded.")
    except Exception as exc:
        print(f"[FATAL] Could not load IndexTTS2: {exc}")
        traceback.print_exc()
        sys.exit(1)

    narrations = load_narration(narration_json)
    if not narrations:
        print(f"[WARNING] No narrations found at {narration_json}. Nothing to do.")
        return

    mode_label = "raw" if USE_RAW_NARRATION else "filtered"
    print(f"[INFO] TTS narration mode: {mode_label}")

    to_generate = []
    for item in narrations:
        image_name = item.get("image")
        text = item.get("narration", "")
        if not image_name or not text:
            continue
        if not USE_RAW_NARRATION:
            text = clean_text_for_tts(text)
        if not text:
            continue
        dst = audio_dir / f"{Path(image_name).stem}.wav"
        if not dst.exists():
            to_generate.append((text, dst))

    if not to_generate:
        print("[INFO] All audio already generated.")
        return

    print(f"[INFO] Generating {len(to_generate)} audio files...")
    for i, (text, path) in enumerate(to_generate, 1):
        print(f"  [{i}/{len(to_generate)}] {path.name}")
        generate_and_save_wav(tts, text, path)

    print("[INFO] Audio generation complete.")


def main() -> None:
    try:
        generate_missing_audio()
    except Exception as exc:
        print(f"[FATAL] Unexpected error: {exc}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
