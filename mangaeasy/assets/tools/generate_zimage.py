"""generate_zimage.py — standalone Z-Image Turbo adapter for mangaEasy.

Copied into the z-image-turbo tool env by `mangaeasy install-tool` and run
with that env's own python (`mangaeasy zimage` does this for you). It has no
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
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate images with Z-Image Turbo.")
    p.add_argument("--prompt", help="Text prompt (English or Chinese; long, descriptive prompts work best).")
    p.add_argument("--prompt-file", type=Path,
                   help="Read the prompt from a UTF-8 file (for long prompts / shell-quoting safety).")
    p.add_argument("--output", type=Path, required=True,
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
    if bool(args.prompt) == bool(args.prompt_file):
        p.error("pass exactly one of --prompt / --prompt-file")
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


def load_pipeline(model: str, strategy: str, torch):
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
        # Safe now: the quantized transformer is ~3.5 GB, not 12.3 GB.
        pipe.enable_model_cpu_offload()
    elif strategy == "bf16":
        pipe = ZImagePipeline.from_pretrained(model, torch_dtype=torch.bfloat16)
        device = "cuda" if torch.cuda.is_available() else "mps"
        pipe.to(device)
    elif strategy == "offload":
        pipe = ZImagePipeline.from_pretrained(model, torch_dtype=torch.bfloat16)
        pipe.enable_sequential_cpu_offload()
    else:  # cpu — works, but expect minutes per image
        print("[zimage] CPU-only fp32 mode: expect several minutes per image", flush=True)
        pipe = ZImagePipeline.from_pretrained(model, torch_dtype=torch.float32)

    print(f"[zimage] pipeline ready in {time.monotonic() - t0:.1f}s", flush=True)
    return pipe


def main() -> int:
    args = parse_args()
    import torch

    prompt = args.prompt or args.prompt_file.read_text(encoding="utf-8").strip()
    width, height = _round16(args.width), _round16(args.height)
    if (width, height) != (args.width, args.height):
        print(f"[zimage] size rounded to multiples of 16: {width}x{height}", flush=True)

    strategy = pick_strategy(torch, args.strategy)
    pipe = load_pipeline(args.model, strategy, torch)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    base_seed = args.seed if args.seed is not None else torch.seed() % (2**31)
    for i in range(args.count):
        seed = base_seed + i
        if args.count == 1:
            out = args.output
        else:
            out = args.output.with_name(f"{args.output.stem}_{i + 1:02d}{args.output.suffix or '.png'}")
        t0 = time.monotonic()
        image = pipe(
            prompt=prompt,
            height=height, width=width,
            num_inference_steps=args.steps,
            guidance_scale=0.0,  # REQUIRED for Turbo — no CFG in the distilled model
            generator=torch.Generator("cpu").manual_seed(seed),
        ).images[0]
        image.save(out)
        print(f"[zimage] {i + 1}/{args.count} seed={seed} {time.monotonic() - t0:.1f}s"
              f" -> SAVED {out.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
