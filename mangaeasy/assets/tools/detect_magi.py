#!/usr/bin/env python3
"""detect_magi.py — standalone MAGI v3 panel-detection adapter.

mangaEasy ships this file and `mangaeasy install-tool magi-v3` copies it into the
external `magi-v3/` tool environment. mangaEasy then runs it from that env:

    python detect_magi.py <image> --out <result.json> [--device auto|cuda|cpu] [--dtype fp16|fp32]

It loads the `ragavsachdeva/magiv3` model via transformers `trust_remote_code`
(the model code and weights are fetched from the Hugging Face Hub on first run),
runs panel detection, and writes:

    {"detections": {"panels": [[x1, y1, x2, y2], ...], ...}}

This is exactly the shape mangaEasy reads back in `mangaeasy.panels.ai`. The
module has no mangaeasy imports so it can run inside the isolated tool env.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MODEL_ID = "ragavsachdeva/magiv3"


def _to_serializable(obj):
    """Convert torch tensors / numpy arrays / scalars into JSON-friendly types."""
    # Lazy, duck-typed conversion so we don't hard-depend on numpy/torch symbols.
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_serializable(v) for v in obj]
    tolist = getattr(obj, "tolist", None)  # numpy arrays, torch tensors
    if callable(tolist):
        return _to_serializable(tolist())
    item = getattr(obj, "item", None)  # numpy / torch scalars
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    return str(obj)


def _resolve_device(pref: str):
    import torch

    if pref == "cpu":
        return "cpu"
    if pref in ("cuda", "auto"):
        return "cuda" if torch.cuda.is_available() else "cpu"
    return "cpu"


def detect(image_path: Path, device_pref: str, dtype_pref: str) -> dict:
    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoModelForCausalLM, AutoProcessor

    device = _resolve_device(device_pref)
    if dtype_pref == "fp16":
        dtype = torch.float16
    elif dtype_pref == "fp32":
        dtype = torch.float32
    else:  # auto: fp16 only makes sense on GPU
        dtype = torch.float16 if device == "cuda" else torch.float32

    print(f"[detect_magi] loading {MODEL_ID} on {device} ({dtype})", flush=True)
    processor = AutoProcessor.from_pretrained(MODEL_ID, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID, torch_dtype=dtype, trust_remote_code=True
    )
    model = model.to(device).eval()

    img = Image.open(image_path).convert("RGB")
    img_array = np.array(img, dtype=np.uint8)

    with torch.no_grad():
        results = model.predict_detections_and_associations([img_array], processor)

    result = results[0] if results else {}
    payload = _to_serializable(result) if isinstance(result, dict) else {}
    payload.setdefault("panels", [])
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="MAGI v3 panel detection (standalone).")
    parser.add_argument("image", type=Path, help="Path to the page image.")
    parser.add_argument("--out", type=Path, required=True, help="Where to write the JSON result.")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--dtype", choices=("auto", "fp16", "fp32"), default="auto")
    args = parser.parse_args()

    if not args.image.exists():
        print(f"[detect_magi] image not found: {args.image}", file=sys.stderr)
        return 2

    try:
        detections = detect(args.image, args.device, args.dtype)
    except Exception as exc:  # surface a clear error to the caller's logs
        print(f"[detect_magi] detection failed: {exc}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"detections": detections}), encoding="utf-8")
    n = len(detections.get("panels", []))
    print(f"[detect_magi] wrote {n} panel(s) -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
