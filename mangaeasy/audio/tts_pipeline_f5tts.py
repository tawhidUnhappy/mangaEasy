#!/usr/bin/env python3
"""mangaeasy.audio.tts_pipeline_f5tts — batch F5-TTS audio generation for the video pipeline.

IMPORTANT: Must run inside the external f5-tts uv environment.
Invoked automatically by mangaeasy video-audio-f5tts.
"""

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

# ── Path bootstrap ─────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parent.parent.parent
_F5_TTS_DIR = Path(
    os.environ.get("F5_TTS_ROOT") or (_PROJECT_ROOT.parent / "f5-tts")
).resolve()

for _p in (_PROJECT_ROOT,):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from mangaeasy.config import HF_CACHE_DIR
from mangaeasy.video_pipeline.common import item_dirs, merge_item_selection, project_name

os.environ["HF_HOME"] = str(HF_CACHE_DIR)
os.environ["HUGGINGFACE_HUB_CACHE"] = str(HF_CACHE_DIR)
os.environ.setdefault("HF_HUB_OFFLINE", "0")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "0")

# CUDA optimizations for RTX 3060 12 GB
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault("CUDA_MODULE_LOADING", "LAZY")
os.environ.setdefault("NVIDIA_TF32_OVERRIDE", "1")

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
    print("  Make sure this runs via: mangaeasy video-audio-f5tts")
    sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch F5-TTS audio generation.")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--audio-root", type=Path, default=Path("audio"))
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--items", nargs="*")
    parser.add_argument("--item-range")
    parser.add_argument("--speaker-wav", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def load_narration(item_dir: Path) -> list[dict]:
    path = item_dir / "narration.json"
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    audio_root = args.audio_root.resolve()
    speaker_wav = args.speaker_wav.resolve()
    name = project_name(project_root, args.project_name)

    if not project_root.exists():
        print(f"[FATAL] Project root not found: {project_root}")
        return 1
    if not speaker_wav.is_file():
        print(f"[FATAL] Speaker WAV not found: {speaker_wav}")
        return 1

    selected = item_dirs(project_root, merge_item_selection(args.items, args.item_range))
    if not selected:
        print(f"[FATAL] No item folders found in {project_root}")
        return 1

    to_generate: list[tuple[str, Path]] = []
    for item_dir in selected:
        item_audio_dir = audio_root / name / item_dir.name
        item_audio_dir.mkdir(parents=True, exist_ok=True)
        narrations = load_narration(item_dir)
        print(f"\n[{item_dir.name}] {len(narrations)} narration item(s) -> {item_audio_dir}", flush=True)
        for item in narrations:
            image_name = item.get("image")
            text = (item.get("narration") or item.get("text") or "").strip()
            if not image_name or not text:
                continue
            dst = item_audio_dir / f"{Path(image_name).stem}.wav"
            if dst.exists() and not args.overwrite:
                continue
            to_generate.append((text, dst))

    if not to_generate:
        print("\n[INFO] All audio already generated.")
        return 0

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\n[INFO] Loading F5-TTS (device: {device}, ref: {speaker_wav.name})...", flush=True)
    f5tts = F5TTS(device=device, hf_cache_dir=str(HF_CACHE_DIR))
    print(f"[INFO] Generating {len(to_generate)} audio files...", flush=True)

    for i, (text, dst) in enumerate(to_generate, 1):
        print(f"  [{i}/{len(to_generate)}] {dst.parent.name}/{dst.name}", flush=True)
        try:
            f5tts.infer(
                ref_file=str(speaker_wav),
                ref_text="",          # auto-transcribed from ref audio
                gen_text=text,
                file_wave=str(dst),
            )
        except Exception as exc:
            print(f"[ERROR] {dst.name}: {exc}")
            traceback.print_exc()

    print(f"\n[INFO] Done. Generated {len(to_generate)} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
