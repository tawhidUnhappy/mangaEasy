#!/usr/bin/env python3
"""mangaeasy.panels.process — prepare panels for video production.

Per-panel pipeline:
  1. Detect text-bubble regions with MAGI v2
  2. Inpaint (erase) speech-bubble text using OpenCV Telea algorithm
  3. Upscale 4× with Real-ESRGAN  (RealESRGAN_x4plus_anime_6B — tuned for manga)
  4. Mirror horizontally (right-to-left → left-to-right reading direction)
  5. Burn a semi-transparent watermark at the bottom-right (if enabled in config)

Output goes to the folder configured in config.system.json → paths.processed_subdir
(default: panels_processed/).  mangaeasy render-video automatically prefers this
folder when it exists.

Settings in config.system.json:
  process_panels.upscale_factor   4          (must be 2 or 4)
  process_panels.mirror           true
  process_panels.hide_bubbles     true
  process_panels.inpaint_radius   5
  watermark.enabled               false
  watermark.text                  "@YourChannel"
  watermark.opacity               0.55       (0–1)
  watermark.font_size             22
  watermark.padding               16

Usage:
    mangaeasy process-panels
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path
from typing import Dict, List

import numpy as np
import torch
from PIL import Image

from mangaeasy.config import PROJECT_ROOT, load_download_config, load_system_config
from mangaeasy.panels.ai import _clamp_box, _run_magi
from mangaeasy.paths import panels_dir, processed_panels_dir

# ---------------------------------------------------------------------------
# Model cache
# ---------------------------------------------------------------------------

_MODELS_DIR = PROJECT_ROOT / ".cache" / "models"

REALESRGAN_MODEL = "RealESRGAN_x4plus_anime_6B"
REALESRGAN_URL   = (
    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/"
    "RealESRGAN_x4plus_anime_6B.pth"
)

# ---------------------------------------------------------------------------
# Real-ESRGAN — lazy singleton
# ---------------------------------------------------------------------------

_upsampler       = None
_upsampler_scale = None


def _ensure_model_file() -> Path:
    model_path = _MODELS_DIR / f"{REALESRGAN_MODEL}.pth"
    if model_path.exists():
        return model_path
    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[process] Downloading {REALESRGAN_MODEL}.pth (~17 MB) …")
    tmp = model_path.with_suffix(".tmp")
    try:
        urllib.request.urlretrieve(REALESRGAN_URL, str(tmp))
        tmp.rename(model_path)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to download Real-ESRGAN model: {exc}") from exc
    print(f"[process] Model saved to {model_path}")
    return model_path


def _get_upsampler(scale: int = 4):
    global _upsampler, _upsampler_scale
    if _upsampler is not None and _upsampler_scale == scale:
        return _upsampler

    # ── Compatibility shim ────────────────────────────────────────────────
    # basicsr/data/degradations.py does:
    #   from torchvision.transforms.functional_tensor import rgb_to_grayscale
    # That private sub-module was removed in torchvision >= 0.15.  The symbol
    # still exists in torchvision.transforms.functional, so we expose it
    # under the old name before basicsr is imported.
    import types as _types
    if "torchvision.transforms.functional_tensor" not in sys.modules:
        import torchvision.transforms.functional as _ftf
        _stub = _types.ModuleType("torchvision.transforms.functional_tensor")
        _stub.rgb_to_grayscale = _ftf.rgb_to_grayscale                     # type: ignore[attr-defined]
        sys.modules["torchvision.transforms.functional_tensor"] = _stub

    try:
        from basicsr.archs.rrdbnet_arch import RRDBNet       # type: ignore
        from realesrgan import RealESRGANer                  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "realesrgan and basicsr are required for panel upscaling.\n"
            "Install them with:  uv add realesrgan basicsr"
        ) from exc

    model_path = _ensure_model_file()
    device     = "cuda" if torch.cuda.is_available() else "cpu"

    # RealESRGAN_x4plus_anime_6B uses a lighter 6-block RRDB
    rrdb = RRDBNet(
        num_in_ch=3, num_out_ch=3,
        num_feat=64, num_block=6, num_grow_ch=32,
        scale=4,
    )
    print(f"[process] Loading {REALESRGAN_MODEL} on {device.upper()} …")
    _upsampler = RealESRGANer(
        scale=4,
        model_path=str(model_path),
        model=rrdb,
        tile=512,        # process in tiles to avoid OOM on large panels
        tile_pad=32,
        pre_pad=0,
        half=(device == "cuda"),
        device=device,
    )
    _upsampler_scale = scale
    print("[process] Real-ESRGAN ready.")
    return _upsampler


# ---------------------------------------------------------------------------
# Speech-bubble inpainting
# ---------------------------------------------------------------------------

def _hide_bubbles(img_bgr: np.ndarray, text_boxes: List[Dict], radius: int) -> np.ndarray:
    """Hide speech-bubble text with a smooth white fill.

    Strategy:
      1. Build a hard mask from each detected text box (with generous padding).
      2. Dilate to push the white fill to the full bubble interior.
      3. Gaussian-blur the mask edges so the fill blends softly — no hard
         rectangular borders visible in the final panel.
      4. Composite the white fill over the image using the blurred mask,
         so the panel art outside the bubble is untouched.
    """
    try:
        import cv2  # type: ignore
    except ImportError:
        print("[WARN] opencv-python not installed — skipping bubble hide.")
        return img_bgr

    if not text_boxes:
        return img_bgr

    H, W = img_bgr.shape[:2]

    # Build mask — pad each box generously so the whole bubble is covered
    pad  = max(radius * 4, 18)
    mask = np.zeros((H, W), dtype=np.uint8)
    for box in text_boxes:
        y1 = max(0, box["y1"] - pad)
        y2 = min(H, box["y2"] + pad)
        x1 = max(0, box["x1"] - pad)
        x2 = min(W, box["x2"] + pad)
        mask[y1:y2, x1:x2] = 255

    # Dilate to merge nearby boxes (catches multi-line bubbles)
    kernel = np.ones((radius * 4 + 1, radius * 4 + 1), np.uint8)
    mask   = cv2.dilate(mask, kernel, iterations=1)

    # Blur edges for a smooth, natural-looking fill boundary
    blur_k = max(21, (radius * 8) | 1)   # must be odd
    alpha  = cv2.GaussianBlur(mask, (blur_k, blur_k), 0).astype(np.float32) / 255.0

    # Blend: white where alpha==1, original where alpha==0
    white  = np.full_like(img_bgr, 255, dtype=np.float32)
    orig   = img_bgr.astype(np.float32)
    a3     = alpha[:, :, np.newaxis]   # broadcast over BGR channels
    result = (white * a3 + orig * (1.0 - a3)).astype(np.uint8)
    return result




# ---------------------------------------------------------------------------
# Per-panel processor
# ---------------------------------------------------------------------------

def process_panel(
    src: Path,
    dst: Path,
    *,
    upscale: bool = True,
    upscale_factor: int = 4,
    mirror: bool = True,
    hide_bubbles: bool = True,
    inpaint_radius: int = 5,
) -> None:

    img = Image.open(src).convert("RGB")
    W, H = img.size

    # ── 1. Detect text bubbles (MAGI v2) ──────────────────────────────────
    text_boxes: List[Dict] = []
    if hide_bubbles:
        raw = _run_magi(np.array(img, dtype=np.uint8))
        text_boxes = [b for entry in raw.get("texts", []) if (b := _clamp_box(entry, W, H))]

    # ── 2. Inpaint speech-bubble regions ──────────────────────────────────
    if hide_bubbles and text_boxes:
        try:
            import cv2 as _cv2
            img_bgr = _cv2.cvtColor(np.array(img), _cv2.COLOR_RGB2BGR)
            img_bgr = _hide_bubbles(img_bgr, text_boxes, inpaint_radius)
            img     = Image.fromarray(_cv2.cvtColor(img_bgr, _cv2.COLOR_BGR2RGB))
        except ImportError:
            pass   # already warned inside _hide_bubbles

    # ── 3. Upscale with Real-ESRGAN ───────────────────────────────────────
    if upscale:
        upsampler = _get_upsampler(upscale_factor)
        img_np    = np.array(img)
        out_np, _ = upsampler.enhance(img_np, outscale=upscale_factor)
        img       = Image.fromarray(out_np)

    # ── 4. Mirror (horizontal flip) ───────────────────────────────────────
    if mirror:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)

    # ── 5. Save ───────────────────────────────────────────────────────────
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, "PNG", optimize=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    dl      = load_download_config()
    syscfg  = load_system_config()
    name    = str(dl["name"])
    chapter = int(dl["chapter"])

    pp_cfg  = syscfg.get("process_panels", {})

    upscale        = bool(pp_cfg.get("upscale",        True))
    upscale_factor = int(pp_cfg.get("upscale_factor", 4))
    mirror         = bool(pp_cfg.get("mirror",         True))
    hide_bubbles   = bool(pp_cfg.get("hide_bubbles",   True))
    inpaint_radius = int(pp_cfg.get("inpaint_radius",  5))

    src_dir = panels_dir(name, chapter)
    dst_dir = processed_panels_dir(name, chapter)

    if not src_dir.exists():
        raise SystemExit(f"[FATAL] Panels folder not found: {src_dir}")

    exts  = (".png", ".jpg", ".jpeg", ".webp")
    srcs  = sorted(
        [f for f in src_dir.iterdir() if f.suffix.lower() in exts],
        key=lambda p: p.name,
    )
    if not srcs:
        raise SystemExit(f"[FATAL] No panel images found in {src_dir}")

    dst_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Manga: {name}  Chapter: {chapter:02d}")
    print(f"[INFO] Source   : {src_dir}")
    print(f"[INFO] Output   : {dst_dir}")
    print(f"[INFO] Upscale  : {'%d×' % upscale_factor if upscale else 'off'}   Mirror: {mirror}   "
          f"Hide bubbles: {hide_bubbles}")
    print(f"[INFO] Panels   : {len(srcs)}")
    print()

    for idx, src in enumerate(srcs, start=1):
        dst = dst_dir / (src.stem + ".png")
        print(f"[{idx:03d}/{len(srcs):03d}] {src.name} → {dst.name}", end="  ", flush=True)
        try:
            process_panel(
                src, dst,
                upscale=upscale,
                upscale_factor=upscale_factor,
                mirror=mirror,
                hide_bubbles=hide_bubbles,
                inpaint_radius=inpaint_radius,
            )
            print("OK")
        except Exception as exc:
            print(f"FAILED — {exc}")
            import traceback

            traceback.print_exc()

    print(f"\n[DONE] {len(srcs)} panels → {dst_dir}")
    print("       Run `mangaeasy render-video` to use the processed panels.")


if __name__ == "__main__":
    main()
