"""Standalone ACE-Step 1.5 adapter copied into its isolated uv project."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def _require_initialized(component: str, result) -> None:
    """Validate ACE-Step's ``(status_message, success)`` init contract."""
    if isinstance(result, tuple):
        status = str(result[0]) if result else "no status returned"
        success = len(result) >= 2 and result[1] is True
    else:
        status = str(result)
        success = result is True
    if not success:
        raise RuntimeError(f"{component} initialization failed: {status}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--lyrics-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260715)
    parser.add_argument("--duration", type=float, default=-1.0)
    parser.add_argument("--language", default="en")
    parser.add_argument("--bpm", type=int)
    parser.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="auto")
    parser.add_argument("--lm-model", default="acestep-5Hz-lm-1.7B")
    parser.add_argument("--lm-backend", choices=("pt", "vllm"), default="pt")
    args = parser.parse_args()

    import torch
    from acestep.handler import AceStepHandler
    from acestep.inference import GenerationConfig, GenerationParams, generate_music
    from acestep.llm_inference import LLMHandler

    device = args.device
    if device == "auto":
        if torch.cuda.is_available():
            device = "cuda"
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"
    project_root = Path(__file__).resolve().parent
    checkpoints = Path(os.environ.get("ACESTEP_CHECKPOINTS_DIR", project_root / "checkpoints")).resolve()
    lyrics = args.lyrics_file.read_text(encoding="utf-8").strip()
    if not lyrics:
        raise ValueError("lyrics file is empty")

    print(f"[ace-step] loading DiT and LM on {device}", flush=True)
    dit = AceStepHandler()
    initialized = dit.initialize_service(
        project_root=str(project_root), config_path="acestep-v15-turbo", device=device,
    )
    _require_initialized("ACE-Step DiT", initialized)
    llm = LLMHandler()
    initialized = llm.initialize(
        checkpoint_dir=str(checkpoints), lm_model_path=args.lm_model,
        backend=args.lm_backend, device=device,
    )
    _require_initialized("ACE-Step LM", initialized)

    params = GenerationParams(
        task_type="text2music", caption=args.prompt, lyrics=lyrics,
        vocal_language=args.language, duration=args.duration, bpm=args.bpm,
        inference_steps=8, shift=3.0, infer_method="ode", thinking=True,
        seed=args.seed,
    )
    config = GenerationConfig(
        batch_size=1, audio_format="wav", use_random_seed=False, seeds=[args.seed],
    )
    generated_dir = args.output.resolve().parent / ".ace-step-generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    result = generate_music(dit, llm, params, config, save_dir=str(generated_dir))
    if not result.success or not result.audios:
        raise RuntimeError(f"ACE-Step generation failed: {result.error}")
    source = Path(result.audios[0]["path"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, args.output)
    print(f"[ace-step] seed={args.seed} -> SAVED {args.output.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
