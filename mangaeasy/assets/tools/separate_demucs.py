"""Run the pinned HTDemucs-ft snapshot without any network model lookup.

This adapter is copied into Demucs' isolated uv environment by
``mediaconductor install-tool demucs``.  The maintained Demucs fork's public
CLI understands Hugging Face model names, but its loader normally resolves
those names through ``hf_hub_download`` at runtime.  We route only the exact
files requested by the pinned model to the already-downloaded local snapshot.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Callable


MODEL_REPO = "adefossez/HTDemucs-ft"
MODEL_NAME = "htdemucs_ft"
MODEL_YAML = f"{MODEL_NAME}.yaml"


def _resolve_device(requested: str, torch_module: object) -> str:
    """Resolve ``auto`` against Torch in this isolated Demucs environment."""
    if requested != "auto":
        return requested
    return "cuda" if torch_module.cuda.is_available() else "cpu"


def _validate_model_dir(model_dir: Path) -> frozenset[str]:
    """Return the exact files Demucs may request, or fail before inference."""
    import yaml

    model_dir = model_dir.resolve()
    manifest = model_dir / MODEL_YAML
    if not manifest.is_file():
        raise FileNotFoundError(
            f"missing pinned Demucs manifest: {manifest}. "
            "Re-run `mediaconductor install-tool demucs`."
        )
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    signatures = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(signatures, list) or not signatures:
        raise RuntimeError(f"invalid Demucs model manifest: {manifest}")

    allowed = {MODEL_YAML}
    for signature in signatures:
        if (
            not isinstance(signature, str)
            or len(signature) != 8
            or any(character not in "0123456789abcdef" for character in signature)
        ):
            raise RuntimeError(f"unsafe model signature in {manifest}: {signature!r}")
        filename = f"{signature}.safetensors"
        if not (model_dir / filename).is_file():
            raise FileNotFoundError(
                f"incomplete pinned Demucs snapshot; missing {model_dir / filename}. "
                "Re-run `mediaconductor install-tool demucs`."
            )
        allowed.add(filename)
    return frozenset(allowed)


def _local_hf_download(model_dir: Path, allowed: frozenset[str]) -> Callable[..., str]:
    """Build a strict ``hf_hub_download`` replacement for one local model."""
    root = model_dir.resolve()

    def download(repo_id: str, filename: str, *_args: object, **_kwargs: object) -> str:
        if repo_id != MODEL_REPO:
            raise RuntimeError(f"network model lookup blocked for unexpected repository: {repo_id}")
        if filename not in allowed or Path(filename).name != filename:
            raise RuntimeError(f"network model lookup blocked for unexpected file: {filename}")
        candidate = (root / filename).resolve()
        if candidate.parent != root or not candidate.is_file():
            raise FileNotFoundError(f"pinned local Demucs model file is unavailable: {candidate}")
        return str(candidate)

    return download


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline HTDemucs-ft vocal separation")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), required=True)
    parser.add_argument("--segment", type=int, default=7)
    args = parser.parse_args()

    audio = args.audio.resolve()
    if not audio.is_file():
        parser.error(f"audio file not found: {audio}")
    model_dir = args.model_dir.resolve()
    allowed = _validate_model_dir(model_dir)

    # Belt and suspenders: Hub clients are put in offline mode, then the only
    # download entry point used by this pinned Demucs fork is replaced with a
    # local, allow-listed resolver.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    import huggingface_hub
    import torch

    huggingface_hub.hf_hub_download = _local_hf_download(model_dir, allowed)
    device = _resolve_device(args.device, torch)

    from demucs.separate import main as demucs_main

    demucs_main([
        "-n", f"hf://adefossez/{MODEL_NAME}",
        "--two-stems", "vocals",
        "--float32",
        "-d", device,
        "--segment", str(args.segment),
        "-j", "1",
        "-o", str(args.output_dir.resolve()),
        str(audio),
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
