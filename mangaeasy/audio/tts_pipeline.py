#!/usr/bin/env python3
"""mangaeasy.audio.tts_pipeline — batch IndexTTS2 audio generation for the video pipeline.

IMPORTANT: Must run inside the external index-tts uv environment.
Invoked automatically by mangaeasy video-audio-indextts.
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
_INDEX_TTS_DIR = Path(
    os.environ.get("INDEX_TTS_ROOT")
    or os.environ.get("INDEX_TTS_DIR")
    or (_PROJECT_ROOT.parent / "index-tts")
).resolve()

for _p in (_PROJECT_ROOT, _INDEX_TTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from mangaeasy.config import HF_CACHE_DIR
from mangaeasy.video_pipeline.common import item_dirs, merge_item_selection, project_name

os.environ["HF_HOME"] = str(HF_CACHE_DIR)
os.environ["HUGGINGFACE_HUB_CACHE"] = str(HF_CACHE_DIR)
os.environ.setdefault("HF_HUB_OFFLINE", "0")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "0")

CHECKPOINTS_DIR = _INDEX_TTS_DIR / "checkpoints"

import torch

try:
    from indextts.infer_v2 import IndexTTS2
except Exception as exc:
    print(f"[FATAL] Could not import IndexTTS2: {exc}")
    print("  Make sure this runs via: mangaeasy video-audio-indextts")
    sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch IndexTTS2 audio generation.")
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
    print(f"\n[INFO] Loading IndexTTS2 (device: {device}, speaker: {speaker_wav.name})...", flush=True)
    tts = IndexTTS2(
        cfg_path=str(CHECKPOINTS_DIR / "config.yaml"),
        model_dir=str(CHECKPOINTS_DIR),
        use_fp16=True,
        use_cuda_kernel=True,
        use_deepspeed=False,
    )
    print(f"[INFO] Generating {len(to_generate)} audio files...", flush=True)

    for i, (text, dst) in enumerate(to_generate, 1):
        print(f"  [{i}/{len(to_generate)}] {dst.parent.name}/{dst.name}", flush=True)
        try:
            tts.infer(
                spk_audio_prompt=str(speaker_wav),
                text=text,
                output_path=str(dst),
                verbose=False,
            )
        except Exception as exc:
            print(f"[ERROR] {dst.name}: {exc}")
            traceback.print_exc()

    print(f"\n[INFO] Done. Generated {len(to_generate)} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
