from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from kokoro import KPipeline


SAMPLE_RATE = 24000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch Kokoro TTS worker for make-video.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--voice", default="af_heart")
    parser.add_argument("--lang", default="a")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="auto")
    parser.add_argument("--split-pattern", default=r"\n+")
    parser.add_argument("--repo-id", default="hexgrad/Kokoro-82M")
    return parser.parse_args()


def _mps_available() -> bool:
    return sys.platform == "darwin" and getattr(torch.backends, "mps", None) is not None \
        and torch.backends.mps.is_available()


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if _mps_available():
        return "mps"
    return "cpu"


def configure_torch(device: str) -> None:
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested, but PyTorch cannot see a CUDA GPU.")
    if device == "mps" and not _mps_available():
        raise RuntimeError("MPS was requested, but PyTorch cannot see an Apple Silicon GPU.")
    if torch.cuda.is_available():
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.benchmark = True
        torch.set_float32_matmul_precision("high")


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array.")
    return data


def synthesize(pipeline: KPipeline, text: str, voice: str, speed: float, split_pattern: str) -> np.ndarray:
    chunks: list[np.ndarray] = []
    with torch.inference_mode():
        for result in pipeline(text, voice=voice, speed=speed, split_pattern=split_pattern):
            if result.audio is None:
                continue
            chunks.append(result.audio.detach().cpu().numpy())
    if not chunks:
        raise RuntimeError("Kokoro produced no audio for a manifest entry.")
    if len(chunks) == 1:
        return chunks[0]
    return np.concatenate(chunks)


def main() -> int:
    args = parse_args()
    device = resolve_device(args.device)
    configure_torch(device)

    print(f"Kokoro device: {device}", flush=True)
    if device == "cuda":
        print(f"GPU: {torch.cuda.get_device_name(0)}", flush=True)
        print(f"Torch CUDA build: {torch.version.cuda}", flush=True)
    print(f"Voice: {args.voice}", flush=True)
    print(f"Language: {args.lang}", flush=True)

    pipeline = KPipeline(lang_code=args.lang, repo_id=args.repo_id, device=device)
    entries = read_manifest(args.manifest)
    chapter_names = list(dict.fromkeys(
        (entry.get("label") or "").split(":", 1)[0] for entry in entries
    ))
    total_chapters = len(chapter_names) or 1
    print(f"MANGAEASY_PROGRESS 0/{total_chapters} Generating audio", flush=True)
    chapters_done = 0
    current_chapter = None
    for index, entry in enumerate(entries, start=1):
        label = entry.get("label") or f"{index}/{len(entries)}"
        chapter = label.split(":", 1)[0]
        if current_chapter is not None and chapter != current_chapter:
            chapters_done += 1
            print(f"MANGAEASY_PROGRESS {chapters_done}/{total_chapters} Generated audio for {current_chapter}", flush=True)
        current_chapter = chapter
        text = (entry.get("text") or "").strip()
        output = Path(entry.get("output") or "")
        if not text or not str(output):
            raise ValueError(f"Bad manifest entry: {entry}")
        output.parent.mkdir(parents=True, exist_ok=True)
        audio = synthesize(pipeline, text, args.voice, args.speed, args.split_pattern)
        sf.write(output, audio, SAMPLE_RATE)
        print(f"[{index:04d}/{len(entries):04d}] {label} -> {output}", flush=True)
    if current_chapter is not None:
        chapters_done += 1
        print(f"MANGAEASY_PROGRESS {chapters_done}/{total_chapters} Generated audio for {current_chapter}", flush=True)

    if device == "cuda":
        torch.cuda.synchronize()
        peak = torch.cuda.max_memory_allocated(0) / 1024**3
        print(f"Peak PyTorch CUDA allocation: {peak:.2f} GiB", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
