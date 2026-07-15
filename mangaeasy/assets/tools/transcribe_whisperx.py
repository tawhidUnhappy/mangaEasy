"""Standalone WhisperX adapter copied into its isolated environment."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


SILERO_HUB_REPO = "snakers4/silero-vad"
DEFAULT_MINIMUM_CONFIDENCE = 0.72


def _minimum_confidence_arg(value: str) -> float:
    try:
        confidence = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "minimum confidence must be a number from 0 to 1"
        ) from exc
    if not 0 <= confidence <= 1:
        raise argparse.ArgumentTypeError(
            "minimum confidence must be a number from 0 to 1"
        )
    return confidence


def _install_offline_silero_hub(torch_module: object) -> None:
    """Serve WhisperX's Silero Torch Hub request from the installed wheel.

    WhisperX 3.8.6 asks ``torch.hub.load`` for Silero even when all Hugging
    Face clients are offline.  The pinned ``silero-vad`` wheel already ships
    the same JIT model, so expose only that exact request and reject every
    other Hub lookup instead of permitting an accidental runtime download.
    """

    def offline_load(
        repo_or_dir: object,
        model: object,
        *_args: object,
        **kwargs: object,
    ) -> tuple[object, tuple[object, object, object, object, object]]:
        repository = str(repo_or_dir).rstrip("/")
        if repository not in {SILERO_HUB_REPO, f"{SILERO_HUB_REPO}:master"}:
            raise RuntimeError(
                "runtime Torch Hub access is disabled for unexpected repository: "
                f"{repo_or_dir}"
            )
        if model != "silero_vad":
            raise RuntimeError(
                f"runtime Torch Hub access is disabled for unexpected model: {model}"
            )
        if kwargs.get("onnx", False):
            raise RuntimeError("the offline WhisperX adapter only provisions Silero JIT")

        from silero_vad import (
            VADIterator,
            collect_chunks,
            get_speech_timestamps,
            load_silero_vad,
            read_audio,
            save_audio,
        )

        return load_silero_vad(onnx=False), (
            get_speech_timestamps,
            save_audio,
            read_audio,
            VADIterator,
            collect_chunks,
        )

    torch_module.hub.load = offline_load


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--align-model", type=Path, required=True)
    parser.add_argument("--language")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument(
        "--minimum-confidence",
        type=_minimum_confidence_arg,
        default=DEFAULT_MINIMUM_CONFIDENCE,
        help="Validated canonical-alignment review threshold forwarded by MediaConductor.",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()

    align_model_path = args.align_model.expanduser().resolve()
    if not (align_model_path / "model.safetensors").is_file():
        raise FileNotFoundError(f"pinned local WhisperX alignment model is incomplete: {align_model_path}")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    import torch

    _install_offline_silero_hub(torch)
    import whisperx

    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    audio = whisperx.load_audio(str(args.audio))
    model = whisperx.load_model(
        args.model, device, compute_type=compute_type, language=args.language,
        vad_method="silero", local_files_only=True,
    )
    result = model.transcribe(audio, batch_size=args.batch_size, language=args.language)
    language = result.get("language") or args.language
    if language != "en":
        raise ValueError(
            "the bundled offline alignment snapshot supports English lyrics; "
            "install and configure a pinned language-specific WhisperX alignment model"
        )
    align_model, metadata = whisperx.load_align_model(
        language_code=language,
        device=device,
        model_name=str(align_model_path),
        model_dir=str(align_model_path.parent),
        model_cache_only=True,
    )
    aligned = whisperx.align(
        result["segments"], align_model, metadata, audio, device,
        return_char_alignments=False,
    )
    aligned["language"] = language
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(aligned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[whisperx] -> SAVED {args.output.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
