"""Standalone Z-Image Turbo adapter for MediaConductor.

Copied into the z-image-turbo tool env by `mediaconductor install-tool` and
run with that env's own Python (`mediaconductor zimage` does this for you). It has no
mangaeasy imports on purpose — the tool env only contains torch/diffusers.

Model: Tongyi-MAI/Z-Image-Turbo (Apache-2.0) — 6B DiT + Qwen3-4B text
encoder. Turbo facts that shape this script (verified against the model card
and community reports, 2026-07):

- ``guidance_scale`` must stay ``0.0`` — the Turbo distillation has no CFG;
  negative prompts are ignored.
- 8-9 inference steps is the intended operating point.
- bfloat16 only on GPU. float16 produces all-black images (NaN latents,
  Tongyi-MAI/Z-Image#14) — never "optimize" this to fp16.
- The bf16 transformer alone is ~12.3 GB, so GPUs under ~15 GB free VRAM
  need NF4 quantization (bitsandbytes, ~7 GB total, ~24 s/image on an
  RTX 3060) or sequential CPU offload (slowest, a few GB VRAM).
- Prompts: English and Chinese, up to 512 tokens; long descriptive prompts
  (scene, subject, attire, lighting, composition) give the best results. It
  renders legible text in images if you quote it in the prompt.

Batch manifests remain text-to-image by default. An entry explicitly using
``generation_mode: "continue-previous"`` is handled by Diffusers'
``ZImageImg2ImgPipeline`` with the named local ``init_image``. The two
pipelines share the already-loaded components; model weights are not loaded a
second time.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path


TEXT_TO_IMAGE = "text-to-image"
CONTINUE_PREVIOUS = "continue-previous"
MIN_CONTINUITY_STRENGTH = 0.35
MAX_CONTINUITY_STRENGTH = 0.65


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate images with Z-Image Turbo.")
    prompt_group = p.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt", help="Text prompt (English or Chinese; long, descriptive prompts work best).")
    prompt_group.add_argument("--prompt-file", type=Path,
                              help="Read the prompt from a UTF-8 file (for long prompts / shell-quoting safety).")
    prompt_group.add_argument("--batch-manifest", type=Path)
    p.add_argument("--output", type=Path,
                   help="Output PNG path. With --count > 1, files get _01.._NN suffixes.")
    p.add_argument("--width", type=int, default=1024)
    p.add_argument("--height", type=int, default=1024)
    p.add_argument("--steps", type=int, default=9,
                   help="Inference steps (8-9 is the Turbo sweet spot; more does not help).")
    p.add_argument("--seed", type=int, default=None, help="Base seed; --count increments it per image.")
    p.add_argument("--count", type=int, default=1, help="Number of variants to generate.")
    p.add_argument("--strategy", choices=("auto", "bf16", "nf4", "offload", "cpu"), default="auto",
                   help="VRAM strategy. auto: bf16 on 15GB+ GPUs / Apple Silicon, NF4 4-bit on smaller "
                        "NVIDIA GPUs, sequential CPU offload as a fallback, plain fp32 on CPU-only machines.")
    p.add_argument("--model", default="Tongyi-MAI/Z-Image-Turbo",
                   help="Local model directory or Hugging Face repo id.")
    args = p.parse_args()
    if not args.batch_manifest and args.output is None:
        p.error("--output is required for a single prompt")
    return args


def _round16(v: int) -> int:
    return max(256, (v // 16) * 16)


def pick_strategy(torch, requested: str) -> str:
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        total = torch.cuda.get_device_properties(0).total_memory
        if total >= 15 * 1024**3:
            return "bf16"
        try:
            import bitsandbytes  # noqa: F401
            return "nf4"
        except Exception:
            print("[zimage] bitsandbytes unavailable; falling back to sequential CPU offload (slow)", flush=True)
            return "offload"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "bf16"
    return "cpu"


def load_pipeline(model: str, strategy: str, torch, *, with_img2img: bool = False):
    from diffusers import ZImagePipeline

    t0 = time.monotonic()
    print(f"[zimage] loading {model} (strategy={strategy}) ...", flush=True)

    if strategy == "nf4":
        from diffusers import BitsAndBytesConfig as DiffusersBnb
        from diffusers import ZImageTransformer2DModel
        from transformers import AutoModel
        from transformers import BitsAndBytesConfig as TransformersBnb

        text_encoder = AutoModel.from_pretrained(
            model, subfolder="text_encoder",
            quantization_config=TransformersBnb(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True,
            ),
            torch_dtype=torch.bfloat16, device_map="auto",
        )
        transformer = ZImageTransformer2DModel.from_pretrained(
            model, subfolder="transformer",
            quantization_config=DiffusersBnb(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True,
                # This module is numerically sensitive; quantizing it degrades output.
                llm_int8_skip_modules=["transformer_blocks.0.img_mod"],
            ),
            torch_dtype=torch.bfloat16,
        )
        pipe = ZImagePipeline.from_pretrained(
            model, text_encoder=text_encoder, transformer=transformer,
            torch_dtype=torch.bfloat16,
        )
    elif strategy == "bf16":
        pipe = ZImagePipeline.from_pretrained(model, torch_dtype=torch.bfloat16)
    elif strategy == "offload":
        pipe = ZImagePipeline.from_pretrained(model, torch_dtype=torch.bfloat16)
    else:  # cpu — works, but expect minutes per image
        print("[zimage] CPU-only fp32 mode: expect several minutes per image", flush=True)
        pipe = ZImagePipeline.from_pretrained(model, torch_dtype=torch.float32)

    img2img_pipe = None
    if with_img2img:
        from diffusers import ZImageImg2ImgPipeline

        # Diffusers' supported from_pipe path shares all model components and
        # does not allocate or load a second copy of the weights. It does,
        # however, silently re-cast shared components to its default dtype
        # (fp32): the 4-bit-quantized modules refuse the cast, but the VAE
        # obeys, and img2img then feeds the transformer-dtype (bf16) image to
        # an fp32 vae.encode(), which conv2d rejects. Pin the dtype the
        # strategy actually uses so the shared VAE keeps it.
        shared_dtype = torch.float32 if strategy == "cpu" else torch.bfloat16
        img2img_pipe = ZImageImg2ImgPipeline.from_pipe(pipe, torch_dtype=shared_dtype)

    if strategy == "nf4":
        # Safe now: the quantized transformer is ~3.5 GB, not 12.3 GB.
        pipe.enable_model_cpu_offload()
        if img2img_pipe is not None:
            # Diffusers requires offload methods to be reapplied to pipelines
            # made with from_pipe. Both Z-Image variants declare the same
            # text_encoder->transformer->vae model execution order.
            img2img_pipe.enable_model_cpu_offload()
    elif strategy == "bf16":
        device = "cuda" if torch.cuda.is_available() else "mps"
        pipe.to(device)
    elif strategy == "offload":
        pipe.enable_sequential_cpu_offload()
        if img2img_pipe is not None:
            img2img_pipe.enable_sequential_cpu_offload()

    print(f"[zimage] pipeline ready in {time.monotonic() - t0:.1f}s", flush=True)
    return pipe, img2img_pipe


def normalize_batch_entries(raw_entries, args, random_seed) -> list[dict]:
    """Validate a batch while preserving the original text-only contract."""
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError("batch manifest must be a non-empty JSON array")
    entries = []
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            raise ValueError(f"batch entry {index + 1} must be an object")
        prompt = raw.get("prompt")
        if not prompt and raw.get("prompt_file"):
            prompt = Path(raw["prompt_file"]).read_text(encoding="utf-8").strip()
        if not isinstance(prompt, str) or not prompt.strip() or not raw.get("output"):
            raise ValueError(f"batch entry {index + 1} needs prompt/prompt_file and output")

        mode = raw.get("generation_mode", TEXT_TO_IMAGE)
        if mode not in {TEXT_TO_IMAGE, CONTINUE_PREVIOUS}:
            raise ValueError(
                f"batch entry {index + 1} generation_mode must be "
                f"'{TEXT_TO_IMAGE}' or '{CONTINUE_PREVIOUS}'"
            )
        entry = {
            "prompt": prompt.strip(),
            "output": Path(raw["output"]),
            "width": int(raw.get("width", args.width)),
            "height": int(raw.get("height", args.height)),
            "steps": int(raw.get("steps", args.steps)),
            "seed": int(raw["seed"] if "seed" in raw else random_seed()),
            "generation_mode": mode,
        }
        if entry["width"] <= 0 or entry["height"] <= 0 or entry["steps"] <= 0:
            raise ValueError(f"batch entry {index + 1} width, height, and steps must be positive")

        if mode == CONTINUE_PREVIOUS:
            if not raw.get("init_image"):
                raise ValueError(f"batch entry {index + 1} continue-previous needs init_image")
            strength = raw.get("strength", 0.45)
            if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength):
                raise ValueError(f"batch entry {index + 1} strength must be a finite number")
            strength = float(strength)
            if not MIN_CONTINUITY_STRENGTH <= strength <= MAX_CONTINUITY_STRENGTH:
                raise ValueError(
                    f"batch entry {index + 1} strength must be between "
                    f"{MIN_CONTINUITY_STRENGTH} and {MAX_CONTINUITY_STRENGTH}"
                )
            init_image = Path(raw["init_image"])
            if init_image.expanduser().resolve() == entry["output"].expanduser().resolve():
                raise ValueError(f"batch entry {index + 1} init_image and output must differ")
            entry["init_image"] = init_image
            entry["strength"] = strength
        elif "init_image" in raw or "strength" in raw:
            raise ValueError(
                f"batch entry {index + 1} init_image/strength require "
                f"generation_mode '{CONTINUE_PREVIOUS}'"
            )
        entries.append(entry)
    return entries


def main() -> int:
    args = parse_args()
    import torch

    if args.batch_manifest:
        raw_entries = json.loads(args.batch_manifest.read_text(encoding="utf-8"))
        entries = normalize_batch_entries(raw_entries, args, lambda: torch.seed() % (2**31))
    else:
        prompt = args.prompt or args.prompt_file.read_text(encoding="utf-8").strip()
        base_seed = args.seed if args.seed is not None else torch.seed() % (2**31)
        entries = []
        for i in range(args.count):
            out = args.output if args.count == 1 else args.output.with_name(
                f"{args.output.stem}_{i + 1:02d}{args.output.suffix or '.png'}"
            )
            entries.append({"prompt": prompt, "output": out, "width": args.width, "height": args.height,
                            "steps": args.steps, "seed": base_seed + i,
                            "generation_mode": TEXT_TO_IMAGE})

    strategy = pick_strategy(torch, args.strategy)
    needs_img2img = any(entry["generation_mode"] == CONTINUE_PREVIOUS for entry in entries)
    pipe, img2img_pipe = load_pipeline(args.model, strategy, torch, with_img2img=needs_img2img)

    for i, entry in enumerate(entries):
        out = entry["output"]
        out.parent.mkdir(parents=True, exist_ok=True)
        width, height = _round16(entry["width"]), _round16(entry["height"])
        seed = entry["seed"]
        t0 = time.monotonic()
        mode = entry["generation_mode"]
        call_args = {
            "prompt": entry["prompt"],
            "height": height,
            "width": width,
            "num_inference_steps": entry["steps"],
            "guidance_scale": 0.0,  # REQUIRED for Turbo — no CFG in the distilled model
            "generator": torch.Generator("cpu").manual_seed(seed),
        }
        if mode == CONTINUE_PREVIOUS:
            from PIL import Image

            init_path = entry["init_image"]
            if not init_path.is_file():
                raise FileNotFoundError(
                    f"continue-previous init image is missing (batch order matters): {init_path}"
                )
            with Image.open(init_path) as source:
                init_image = source.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
            call_args.update(image=init_image, strength=entry["strength"])
            image = img2img_pipe(**call_args).images[0]
            source_note = f" init={init_path.resolve()} strength={entry['strength']:.2f}"
        else:
            image = pipe(**call_args).images[0]
            source_note = ""
        image.save(out)
        print(f"[zimage] {i + 1}/{len(entries)} mode={mode}{source_note} seed={seed} "
              f"{time.monotonic() - t0:.1f}s"
              f" -> SAVED {out.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
