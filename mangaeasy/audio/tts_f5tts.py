#!/usr/bin/env python3
"""mangaeasy.audio.tts_f5tts — generate narration audio via F5-TTS.

IMPORTANT: Must run inside the external f5-tts uv environment.
Invoked automatically by mangaeasy f5-tts.
"""

import os
import sys
import traceback
from pathlib import Path

# ── Path bootstrap ─────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve()                        # mangaeasy/audio/tts_f5tts.py
_PROJECT_ROOT = _HERE.parent.parent.parent              # project root
_F5_TTS_DIR = Path(
    os.environ.get("F5_TTS_ROOT") or (_PROJECT_ROOT.parent / "f5-tts")
).resolve()

for _p in (_PROJECT_ROOT,):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ── Config ────────────────────────────────────────────────────────────────────
from mangaeasy.config import PROJECT_ROOT, HF_CACHE_DIR, load_system_config
from mangaeasy.narration import load_narration
from mangaeasy.narration.clean import clean_text_for_tts
from mangaeasy.paths import audio_dir as _audio_dir, narration_json as _narration_json

os.environ["HF_HOME"] = str(HF_CACHE_DIR)
os.environ["HUGGINGFACE_HUB_CACHE"] = str(HF_CACHE_DIR)
os.environ.setdefault("HF_HUB_OFFLINE", "0")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "0")

# CUDA optimizations for RTX 3060 12 GB
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault("CUDA_MODULE_LOADING", "LAZY")
os.environ.setdefault("NVIDIA_TF32_OVERRIDE", "1")

_tts_cfg = load_system_config().get("tts", {})
SPEAKER_WAV = PROJECT_ROOT / _tts_cfg.get("speaker_wav", "vocal/manga[vocal2].wav")
USE_RAW_NARRATION = bool(_tts_cfg.get("use_raw_narration", False))

# ── Import F5-TTS ─────────────────────────────────────────────────────────────
import torch

# torchaudio 2.9+ unconditionally delegates torchaudio.load() to torchcodec,
# which has no Windows wheels. Redirect to soundfile before F5-TTS imports torchaudio.
import soundfile as _sf
import torchaudio as _torchaudio

def _sf_load(uri, frame_offset=0, num_frames=-1, normalize=True,
             channels_first=True, format=None, backend=None, buffer_size=4096):
    start = frame_offset if frame_offset else 0
    stop = (frame_offset + num_frames) if num_frames > 0 else None
    data, sr = _sf.read(str(uri), dtype="float32", always_2d=True, start=start, stop=stop)
    tensor = torch.from_numpy(data.T if channels_first else data)
    return tensor, sr

_torchaudio.load = _sf_load

try:
    from f5_tts.api import F5TTS
except Exception as exc:
    print(f"[FATAL] Could not import F5TTS: {exc}")
    print("  Make sure you are running from the external f5-tts uv environment:")
    print("    mangaeasy f5-tts")
    sys.exit(1)


_f5tts: "F5TTS | None" = None


def _get_model() -> "F5TTS":
    global _f5tts
    if _f5tts is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[INFO] Loading F5-TTS (device: {device}, ref: {SPEAKER_WAV.name})...", flush=True)
        _f5tts = F5TTS(device=device, hf_cache_dir=str(HF_CACHE_DIR))
        print("[INFO] F5-TTS loaded.", flush=True)
    return _f5tts


def generate_and_save_wav(text: str, out_path: Path) -> None:
    try:
        _get_model().infer(
            ref_file=str(SPEAKER_WAV),
            ref_text="",          # auto-transcribed from ref audio
            gen_text=text,
            file_wave=str(out_path),
        )
    except Exception as exc:
        print(f"[ERROR] Failed to generate {out_path.name}: {exc}")
        traceback.print_exc()


# ── Main ──────────────────────────────────────────────────────────────────────

def generate_missing_audio() -> None:
    from mangaeasy.config import load_download_config
    dl_cfg = load_download_config()
    chapter = int(dl_cfg["chapter"])
    name = str(dl_cfg["name"])

    audio_dir = _audio_dir(name, chapter)
    narration_json = _narration_json(name, chapter)
    audio_dir.mkdir(parents=True, exist_ok=True)

    print("[INFO] Starting F5-TTS audio generation...")

    if not SPEAKER_WAV.is_file():
        print(f"[FATAL] Speaker reference missing: {SPEAKER_WAV}")
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
        generate_and_save_wav(text, path)

    print("[INFO] Audio generation complete.")


def main() -> None:
    try:
        generate_missing_audio()
    except Exception as exc:
        print(f"[FATAL] Unexpected error: {exc}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
