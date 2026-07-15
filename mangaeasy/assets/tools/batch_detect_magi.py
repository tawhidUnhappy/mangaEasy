#!/usr/bin/env python3
"""batch_detect_magi.py — MAGI v3 panel detection over a whole folder, model loaded once.

MediaConductor ships this file and `mediaconductor install-tool magi-v3` copies it into the
external `magi-v3/` tool environment (alongside `detect_magi.py`). `mangaeasy
page-split` runs it from that env:

    python batch_detect_magi.py <pages_dir> --out <detections.json> [--device auto|cuda|cpu]

Unlike `detect_magi.py` (one image, model reloaded per call), this loads the
`ragavsachdeva/magiv3` model **once** and loops every page in the folder — the
whole-chapter path. It writes:

    {"<page filename>": {"size": [W, H], "panels": [[x1, y1, x2, y2], ...]}, ...}

and prints one `[batch_detect] i/N <name>: <k> panels` line per page so the
caller can stream live progress.

Production-verified env pins (see docs/history/legacy-inventory.md and
CLAUDE.md / the magi-v3 memory) — this script bakes them in so it works on the
stock managed env:
  * `attn_implementation="eager"` is required (SDPA path raises on Florence2).
  * transformers 4.48.3 is the known-good line; 4.57.x removes `generate`.

Has no mangaeasy imports so it runs inside the isolated tool env.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MODEL_ID = "ragavsachdeva/magiv3"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _resolve_device(pref: str) -> str:
    import torch

    if pref == "cpu":
        return "cpu"
    if pref in ("cuda", "auto"):
        return "cuda" if torch.cuda.is_available() else "cpu"
    return "cpu"


def _numeric_key(p: Path):
    """Sort pages the way a human reads them: by the trailing number if present."""
    import re

    nums = re.findall(r"\d+", p.stem)
    return (tuple(int(n) for n in nums) if nums else (), p.name)


def main() -> int:
    parser = argparse.ArgumentParser(description="MAGI v3 panel detection over a folder (model loaded once).")
    parser.add_argument("pages_dir", type=Path, help="Folder of page images.")
    parser.add_argument("--out", type=Path, required=True, help="Where to write the detections JSON.")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--dtype", choices=("auto", "fp16", "fp32"), default="auto")
    args = parser.parse_args()

    if not args.pages_dir.is_dir():
        print(f"[batch_detect] pages dir not found: {args.pages_dir}", file=sys.stderr)
        return 2

    pages = sorted(
        (p for p in args.pages_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS),
        key=_numeric_key,
    )
    if not pages:
        print(f"[batch_detect] no page images in {args.pages_dir}", file=sys.stderr)
        return 2

    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoModelForCausalLM, AutoProcessor

    device = _resolve_device(args.device)
    if args.dtype == "fp16":
        dtype = torch.float16
    elif args.dtype == "fp32":
        dtype = torch.float32
    else:
        dtype = torch.float16 if device == "cuda" else torch.float32

    print(f"[batch_detect] loading {MODEL_ID} on {device} ({dtype})", flush=True)
    processor = AutoProcessor.from_pretrained(MODEL_ID, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        torch_dtype=dtype,
        trust_remote_code=True,
        attn_implementation="eager",
    ).to(device).eval()

    results: dict = {}
    for i, page in enumerate(pages, 1):
        try:
            img = Image.open(page).convert("RGB")
        except Exception as exc:
            print(f"[batch_detect] {i}/{len(pages)} {page.name}: SKIP ({exc})", flush=True)
            continue
        arr = np.array(img, dtype=np.uint8)
        with torch.no_grad():
            dets = model.predict_detections_and_associations([arr], processor)
        raw = (dets[0].get("panels", []) if dets else []) or []
        panels = [[float(v) for v in box[:4]] for box in raw]
        results[page.name] = {"size": [img.width, img.height], "panels": panels}
        print(f"[batch_detect] {i}/{len(pages)} {page.name}: {len(panels)} panels", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"[batch_detect] wrote {len(results)} page(s) -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
