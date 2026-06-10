"""mangaeasy.panels.gutter
Gutter-based panel detection for webtoons / webcomics.

GPU-accelerated (optional): if PyTorch + CUDA is available, the per-row
match math runs on the GPU; everything else is CPU / PIL.

Also contains a CLI entry point (`mangaeasy gutter-split`) for standalone use.
"""

from __future__ import annotations

import argparse
import math
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Literal, Optional, Tuple

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

DeviceMode = Literal["auto", "cpu", "cuda"]


# ---------------------------------------------------------------------------
# Torch import (optional)
# ---------------------------------------------------------------------------

def _torch_try_import():
    try:
        import torch  # type: ignore
        return torch
    except Exception:
        return None


def choose_device(device: DeviceMode = "auto") -> Tuple[str, Optional[str]]:
    """Return (resolved_device, gpu_name)."""
    torch = _torch_try_import()
    if device == "cpu" or torch is None:
        return "cpu", None
    if device == "cuda":
        if torch.cuda.is_available():
            try:
                return "cuda", torch.cuda.get_device_name(0)
            except Exception:
                return "cuda", None
        return "cpu", None
    # auto
    if torch.cuda.is_available():
        try:
            return "cuda", torch.cuda.get_device_name(0)
        except Exception:
            return "cuda", None
    return "cpu", None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GutterConfig:
    gutter_solidity_threshold: float = 0.97
    perfect_gutter_threshold: float = 0.995
    min_gutter_height: int = 12
    tolerance: int = 8
    min_panel_height: int = 80
    padding: int = 2
    max_color_sample_rows: int = 1000
    max_width_samples: int = 650
    candidate_colors: int = 8


def load_gutter_config(config_path: Path) -> GutterConfig:
    import json
    if not config_path.exists():
        return GutterConfig()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return GutterConfig()

    def g(key: str, default):
        return data.get(key, default)

    return GutterConfig(
        gutter_solidity_threshold=float(g("GUTTER_SOLIDITY_THRESHOLD", GutterConfig.gutter_solidity_threshold)),
        perfect_gutter_threshold=float(g("PERFECT_GUTTER_THRESHOLD", GutterConfig.perfect_gutter_threshold)),
        min_gutter_height=int(g("MIN_GUTTER_HEIGHT", GutterConfig.min_gutter_height)),
        tolerance=int(g("TOLERANCE", GutterConfig.tolerance)),
        min_panel_height=int(g("MIN_PANEL_HEIGHT", GutterConfig.min_panel_height)),
        padding=int(g("PADDING", GutterConfig.padding)),
    )


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _get_padding_color(img_rgb: Image.Image) -> Tuple[int, int, int]:
    arr = np.array(img_rgb)
    h, w = arr.shape[:2]
    if w == 0 or h == 0:
        return (255, 255, 255)
    edge_width = min(5, max(1, w // 50))
    left = arr[:, :edge_width, :].reshape(-1, 3)
    right = arr[:, -edge_width:, :].reshape(-1, 3)
    edge_pixels = np.vstack([left, right])
    colors, counts = np.unique(edge_pixels, axis=0, return_counts=True)
    dominant = colors[counts.argmax()]
    return tuple(int(c) for c in dominant)


def _numeric_key(name: str) -> Tuple[int, str]:
    import re
    nums = re.findall(r"\d+", Path(name).stem)
    return (int(nums[-1]) if nums else -1, name)


def collect_image_paths(input_dir: Path, sort_mode: str = "numeric") -> List[Path]:
    exts = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    files = [p for p in input_dir.iterdir() if p.suffix.lower() in exts and p.is_file()]
    if sort_mode == "numeric":
        files.sort(key=lambda p: _numeric_key(p.name))
    elif sort_mode == "lex":
        files.sort(key=lambda p: p.name.lower())
    return files


def stitch_images(image_paths: Iterable[Path], canvas_width: Optional[int] = None) -> Image.Image:
    paths = list(image_paths)
    if not paths:
        raise FileNotFoundError("No images provided to stitch.")
    if canvas_width is None:
        widths: Counter = Counter()
        for p in paths:
            with Image.open(p) as im:
                widths[im.width] += 1
        canvas_width = widths.most_common(1)[0][0]

    processed = []
    total_h = 0
    for p in paths:
        img = Image.open(p)
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        if img.width > canvas_width:
            left = (img.width - canvas_width) // 2
            img = img.crop((left, 0, left + canvas_width, img.height))
        elif img.width < canvas_width:
            pad_color = _get_padding_color(img.convert("RGB"))
            new_img = Image.new("RGBA", (canvas_width, img.height), pad_color + (255,))
            left = (canvas_width - img.width) // 2
            new_img.paste(img, (left, 0), img)
            img = new_img
        processed.append(img)
        total_h += img.height

    combined = Image.new("RGBA", (canvas_width, total_h), (255, 255, 255, 255))
    y = 0
    for img in processed:
        combined.paste(img, (0, y), img)
        y += img.height
    return combined.convert("RGB")


# ---------------------------------------------------------------------------
# Solidity math (CPU + GPU variants)
# ---------------------------------------------------------------------------

def _stride_for_max_samples(w: int, max_samples: int) -> int:
    return max(1, int(math.ceil(w / max_samples)))


def _match_pct_cpu(arr: np.ndarray, gutter_color: Tuple[int, int, int], cfg: GutterConfig) -> np.ndarray:
    h, w = arr.shape[:2]
    stride = _stride_for_max_samples(w, cfg.max_width_samples)
    arr_s = arr[:, ::stride, :].astype(np.int16, copy=False)
    color = np.array(gutter_color, dtype=np.int16)
    matches = np.all(np.abs(arr_s - color) <= cfg.tolerance, axis=2)
    return matches.mean(axis=1).astype(np.float32, copy=False)


def _match_pct_torch(arr: np.ndarray, gutter_color: Tuple[int, int, int], cfg: GutterConfig, device: str) -> np.ndarray:
    torch = _torch_try_import()
    if torch is None:
        return _match_pct_cpu(arr, gutter_color, cfg)
    h, w = arr.shape[:2]
    stride = _stride_for_max_samples(w, cfg.max_width_samples)
    arr_s = arr[:, ::stride, :]
    x = torch.from_numpy(arr_s).to(device=device).to(dtype=torch.int16)
    c = torch.tensor(gutter_color, dtype=torch.int16, device=device).view(1, 1, 3)
    matches = (torch.abs(x - c) <= int(cfg.tolerance)).all(dim=2)
    return matches.to(dtype=torch.float32).mean(dim=1).detach().cpu().numpy().astype(np.float32, copy=False)


def _split_from_match_pct(match_pct: np.ndarray, cfg: GutterConfig) -> Tuple[List[Tuple[int, int]], int]:
    h = int(match_pct.shape[0])
    is_solid = match_pct >= cfg.gutter_solidity_threshold
    raw: List[Tuple[int, int]] = []
    in_panel = False
    start_row = 0
    gutter_run = 0
    anchor_hits = 0
    for y in range(h):
        if bool(is_solid[y]):
            gutter_run += 1
            continue
        if gutter_run >= cfg.min_gutter_height and in_panel:
            gutter_start = y - gutter_run
            block = match_pct[gutter_start:y]
            if bool(np.any(block >= cfg.perfect_gutter_threshold)):
                anchor_hits += int(np.sum(block >= cfg.perfect_gutter_threshold))
                end_row = gutter_start
                if end_row - start_row >= cfg.min_panel_height:
                    raw.append((start_row, end_row))
                in_panel = False
        gutter_run = 0
        if not in_panel:
            in_panel = True
            start_row = y
    if in_panel and (h - start_row) >= cfg.min_panel_height:
        raw.append((start_row, h))
    if not raw:
        return [], 0
    padded: List[Tuple[int, int]] = []
    for t, b in raw:
        t2 = max(0, t - cfg.padding)
        b2 = min(h, b + cfg.padding)
        if not padded:
            if b2 - t2 >= cfg.min_panel_height:
                padded.append((t2, b2))
        else:
            prev_b = padded[-1][1]
            if t2 < prev_b:
                t2 = prev_b
            if b2 - t2 >= cfg.min_panel_height:
                padded.append((t2, b2))
    score = len(padded) * 10000 + anchor_hits
    return padded, score


def _dist_to_white(color: Tuple[int, int, int]) -> int:
    return sum(abs(c - 255) for c in color)

def _dist_to_black(color: Tuple[int, int, int]) -> int:
    return sum(color)

def _pick_candidate_colors(color_counts: Counter, top_n: int) -> List[Tuple[int, int, int]]:
    if not color_counts:
        return [(255, 255, 255)]
    common = [tuple(map(int, c)) for c, _ in color_counts.most_common(top_n)]
    white_best = min(common, key=_dist_to_white)
    black_best = min(common, key=_dist_to_black)
    candidates: List[Tuple[int, int, int]] = []
    if _dist_to_white(white_best) < 120:
        candidates.append(white_best)
    if _dist_to_black(black_best) < 120 and black_best not in candidates:
        candidates.append(black_best)
    for c in common:
        if c not in candidates:
            candidates.append(c)
    return candidates


def _split_with_color(
    arr: np.ndarray, gutter_color: Tuple[int, int, int], cfg: GutterConfig, resolved_device: str
) -> Tuple[List[Tuple[int, int]], int]:
    if resolved_device == "cuda":
        match_pct = _match_pct_torch(arr, gutter_color, cfg, device=resolved_device)
    else:
        match_pct = _match_pct_cpu(arr, gutter_color, cfg)
    return _split_from_match_pct(match_pct, cfg)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def gutter_split_ranges(
    panel_img: Image.Image, cfg: GutterConfig, *, device: DeviceMode = "auto"
) -> List[Tuple[int, int]]:
    """Split an image into panel y-ranges using gutter detection."""
    arr = np.array(panel_img.convert("RGB"))
    h, w = arr.shape[:2]
    if h <= 1 or w <= 1:
        return []
    resolved_device, _ = choose_device(device)
    sample_n = min(h, cfg.max_color_sample_rows)
    sample_idx = np.linspace(0, h - 1, num=sample_n, dtype=int)
    row_means = np.round(arr[sample_idx].mean(axis=1)).astype(int)
    color_counts: Counter = Counter(map(tuple, row_means))
    candidates = _pick_candidate_colors(color_counts, cfg.candidate_colors)
    best_ranges: List[Tuple[int, int]] = []
    best_score = -1
    for c in candidates:
        ranges, score = _split_with_color(arr, c, cfg, resolved_device)
        if score > best_score:
            best_score = score
            best_ranges = ranges
    return best_ranges


@dataclass
class _Segment:
    img: Image.Image
    offset_y: int
    height: int


def auto_detect_panels(
    image_dir: Path,
    cfg: GutterConfig,
    *,
    sort_mode: str = "numeric",
    device: DeviceMode = "auto",
    return_device_info: bool = False,
):
    """Detect panels from all images in a directory.

    Returns list of {"top": int, "bottom": int} dicts in stitched coordinates.
    If return_device_info=True, returns (panels, {"device":..., "gpu_name":...}).
    """
    paths = collect_image_paths(image_dir, sort_mode=sort_mode)
    resolved_device, gpu_name = choose_device(device)
    info = {"device": resolved_device, "gpu_name": gpu_name}

    if not paths:
        return ([], info) if return_device_info else []

    combined = stitch_images(paths)
    queue: List[_Segment] = [_Segment(combined, 0, combined.height)]
    finals: List[Tuple[int, int]] = []

    while queue:
        seg = queue.pop(0)
        ranges = gutter_split_ranges(seg.img, cfg, device=resolved_device)
        if not ranges:
            if seg.height >= cfg.min_panel_height:
                finals.append((seg.offset_y, seg.offset_y + seg.height))
            continue
        if len(ranges) > 1:
            children = [
                _Segment(seg.img.crop((0, t, seg.img.width, b)), seg.offset_y + t, b - t)
                for t, b in ranges
            ]
            queue = children + queue
        else:
            t, b = ranges[0]
            finals.append((seg.offset_y + t, seg.offset_y + b))

    finals.sort(key=lambda x: x[0])
    out = []
    last_b = -1
    for t, b in finals:
        if b - t < cfg.min_panel_height:
            continue
        if last_b != -1 and t < last_b:
            t = last_b
        if b - t < cfg.min_panel_height:
            continue
        out.append({"top": int(t), "bottom": int(b)})
        last_b = int(b)

    return (out, info) if return_device_info else out


# ---------------------------------------------------------------------------
# CLI entry point  (mangaeasy gutter-split)
# ---------------------------------------------------------------------------

def _recursive_ranges(img: Image.Image, cfg: GutterConfig, device: str) -> List[Tuple[int, int]]:
    queue = [(img, 0, img.height)]
    finals: List[Tuple[int, int]] = []
    while queue:
        seg_img, off, h = queue.pop(0)
        ranges = gutter_split_ranges(seg_img, cfg, device=device)
        if not ranges:
            if h >= cfg.min_panel_height:
                finals.append((off, off + h))
            continue
        if len(ranges) > 1:
            children = [(seg_img.crop((0, t, seg_img.width, b)), off + t, b - t) for t, b in ranges]
            queue = children + queue
        else:
            t, b = ranges[0]
            finals.append((off + t, off + b))
    finals.sort(key=lambda x: x[0])
    out: List[Tuple[int, int]] = []
    last_b = -1
    for t, b in finals:
        if b - t < cfg.min_panel_height:
            continue
        if last_b != -1 and t < last_b:
            t = last_b
        if b - t < cfg.min_panel_height:
            continue
        out.append((t, b))
        last_b = b
    return out


def _pipeline(input_dir: Path, output_dir: Path, cfg: GutterConfig, *, sort_mode: str = "numeric", device: str = "auto") -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = collect_image_paths(input_dir, sort_mode=sort_mode)
    if not paths:
        raise FileNotFoundError(f"No images found in {input_dir}")
    resolved_device, gpu_name = choose_device(device)
    dev_msg = resolved_device + (f" ({gpu_name})" if gpu_name else "")
    print(f"[INFO] Device: {dev_msg}")
    combined = stitch_images(paths)
    ranges = _recursive_ranges(combined, cfg, resolved_device)
    print(f"Detected {len(ranges)} panel(s). Saving...")
    for i, (t, b) in enumerate(ranges, start=1):
        panel = combined.crop((0, t, combined.width, b))
        out_path = output_dir / f"{i:03d}.jpg"
        if panel.height > 65535:
            print(f"[Skip] {out_path.name}: height {panel.height}px exceeds JPEG limit.")
            continue
        panel.convert("RGB").save(out_path, "JPEG", quality=95, optimize=True)
    print(f"Saved {len(ranges)} panel(s) to {output_dir}")


def main() -> None:
    from mangaeasy.config import PROJECT_ROOT
    ap = argparse.ArgumentParser(description="Gutter-based webtoon panel splitter")
    ap.add_argument("--input", default="./tmp/download")
    ap.add_argument("--output", default="./tmp/cropped_panels")
    ap.add_argument("--config", default=str(PROJECT_ROOT / "config.json"))
    ap.add_argument("--sort", default="numeric", choices=["numeric", "lex", "none"])
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    args = ap.parse_args()
    cfg = load_gutter_config(Path(args.config))
    _pipeline(Path(args.input), Path(args.output), cfg, sort_mode=args.sort, device=args.device)


if __name__ == "__main__":
    main()
