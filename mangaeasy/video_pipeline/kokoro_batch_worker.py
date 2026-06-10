from __future__ import annotations

import argparse
import json
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
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--split-pattern", default=r"\n+")
    parser.add_argument("--repo-id", default="hexgrad/Kokoro-82M")
    return parser.parse_args()


def resolve_device(requested: str) -> str:
    if requested == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return requested


def configure_torch(device: str) -> None:
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested, but PyTorch cannot see a CUDA GPU.")
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
    for index, entry in enumerate(entries, start=1):
        label = entry.get("label") or f"{index}/{len(entries)}"
        text = (entry.get("text") or "").strip()
        output = Path(entry.get("output") or "")
        if not text or not str(output):
            raise ValueError(f"Bad manifest entry: {entry}")
        output.parent.mkdir(parents=True, exist_ok=True)
        audio = synthesize(pipeline, text, args.voice, args.speed, args.split_pattern)
        sf.write(output, audio, SAMPLE_RATE)
        print(f"[{index:04d}/{len(entries):04d}] {label} -> {output}", flush=True)

    if device == "cuda":
        torch.cuda.synchronize()
        peak = torch.cuda.max_memory_allocated(0) / 1024**3
        print(f"Peak PyTorch CUDA allocation: {peak:.2f} GiB", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
