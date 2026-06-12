#!/usr/bin/env python3
"""mangaeasy.ocr.vision_pipeline — batch panel analysis for manga chapters (MAGI v3).

IMPORTANT: Run inside the external magi-v3 uv environment (transformers >= 5.0.0).

Auto mode (reads name/chapter from config.json):
    uv run --directory ../magi-v3 python mangaeasy/ocr/vision_pipeline.py --auto

Manual mode (explicit chapter directory):
    uv run --directory ../magi-v3 python mangaeasy/ocr/vision_pipeline.py <chapter_dir>

Auto mode scans ./library/{name}/{chapter}/panels/ and writes to
./library/{name}/{chapter}/narration_{chapter}.json.
Manual mode reads narration.json from <chapter_dir> and resolves images from there.

Existing fields are never overwritten — re-run is safe.

New fields added (never collide with existing "image" / "narration"):
  magi_panels            — [[x1,y1,x2,y2], ...] detected panel boxes
  magi_characters        — [[x1,y1,x2,y2], ...] character bounding boxes
  magi_character_clusters— [int, ...]  cluster ID per character (same ID = same person)
  magi_texts             — [[x1,y1,x2,y2], ...] speech-bubble bounding boxes
  magi_text_ocr          — ["...", ...]  per-bubble OCR text from MAGI
  magi_associations      — [[text_idx, char_idx], ...] bubble-to-speaker links
  magi_is_essential      — [bool, ...]  True = story text, False = SFX/background
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings
from pathlib import Path
from typing import Optional

# ── Path bootstrap ────────────────────────────────────────────────────────────
# When called via "uv run --directory ../magi-v3 python mangaeasy/ocr/vision_pipeline.py",
# the CWD is magi-v3/ but __file__ resolves to the actual source location.
_HERE         = Path(__file__).resolve()
_PROJECT_ROOT = _HERE.parent.parent.parent  # mangaeasy/ocr/vision_pipeline.py → root

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── Config (sets HF_HOME before any ML import) ───────────────────────────────
from mangaeasy.config import HF_CACHE_DIR, load_download_config
from mangaeasy.narration import load_narration, save_narration
from mangaeasy.paths import panels_dir as _panels_dir, narration_json as _narration_json
from mangaeasy.utils import numeric_sort_key

os.environ.setdefault("HF_HOME",      str(HF_CACHE_DIR))
os.environ.setdefault("HF_HUB_CACHE", str(HF_CACHE_DIR / "hub"))

import numpy as np
import torch
from PIL import Image

# ---------------------------------------------------------------------------
# Model ID
# ---------------------------------------------------------------------------

MAGI_MODEL_ID = "ragavsachdeva/magiv3"

# ---------------------------------------------------------------------------
# Lazy model singleton
# ---------------------------------------------------------------------------

_magi_model     = None
_magi_processor = None


def _get_device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


def _load_magi() -> bool:
    global _magi_model, _magi_processor
    if _magi_model is not None:
        return True
    try:
        from transformers import AutoModelForCausalLM, AutoProcessor

        print(f"[vision] Loading MAGI v3 ({MAGI_MODEL_ID}) …")
        device = _get_device()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore")
            _magi_processor = AutoProcessor.from_pretrained(
                MAGI_MODEL_ID, trust_remote_code=True
            )
            _magi_model = AutoModelForCausalLM.from_pretrained(
                MAGI_MODEL_ID,
                torch_dtype=torch.float16,
                trust_remote_code=True,
            ).to(device).eval()
        print(f"[vision] MAGI v3 ready on {device.upper()}")
        return True
    except Exception as exc:
        print(f"[vision] MAGI v3 failed to load: {exc}")
        return False


# ---------------------------------------------------------------------------
# Inference helpers
# ---------------------------------------------------------------------------

def _run_magi(img: Image.Image) -> dict:
    arr = np.array(img.convert("RGB"), dtype=np.uint8)
    try:
        with torch.no_grad():
            results = _magi_model.predict_detections_and_associations(
                [arr], _magi_processor
            )
        return results[0] if results else {}
    except Exception as exc:
        print(f"[vision] MAGI inference error: {exc}")
        return {}


def _run_magi_ocr(img: Image.Image) -> dict:
    arr = np.array(img.convert("RGB"), dtype=np.uint8)
    try:
        with torch.no_grad():
            results = _magi_model.predict_ocr([arr], _magi_processor)
        return results[0] if results else {}
    except Exception as exc:
        print(f"[vision] MAGI OCR error: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Per-entry fields
# ---------------------------------------------------------------------------

_MAGI_FIELDS = {
    "magi_panels", "magi_characters", "magi_character_clusters",
    "magi_texts", "magi_text_ocr", "magi_associations", "magi_is_essential",
}


def _entry_needs_magi(entry: dict) -> bool:
    return any(entry.get(k) is None for k in _MAGI_FIELDS)


def _find_image(chapter_dir: Path, image_name: str) -> Optional[Path]:
    for candidate in (
        chapter_dir / image_name,
        chapter_dir / "panels" / image_name,
        chapter_dir / "panels_filename" / image_name,
        chapter_dir / "download" / image_name,
    ):
        if candidate.exists():
            return candidate
    return None


def _apply_magi(entry: dict, img: Image.Image) -> None:
    det = _run_magi(img)
    ocr = _run_magi_ocr(img)

    def to_list(boxes) -> list:
        return [[int(v) for v in b] for b in (boxes or [])]

    entry["magi_panels"]             = to_list(det.get("panels", []))
    entry["magi_characters"]         = to_list(det.get("characters", []))
    entry["magi_character_clusters"] = [int(c) for c in (det.get("character_cluster_labels") or [])]
    entry["magi_texts"]              = to_list(det.get("texts", []))
    entry["magi_text_ocr"]           = list(ocr.get("ocr_texts") or [])
    raw_assoc                        = det.get("text_character_associations") or []
    entry["magi_associations"]       = [[int(a[0]), int(a[1])] for a in raw_assoc]
    essential                        = det.get("is_essential_text")
    entry["magi_is_essential"]       = [bool(v) for v in (essential or [])]


# ---------------------------------------------------------------------------
# Chapter processor
# ---------------------------------------------------------------------------

def process_chapter(
    chapter_dir: Path,
    batch_size: int = 4,
) -> None:
    narration_path = chapter_dir / "narration.json"
    if not narration_path.exists():
        print(f"[vision] narration.json not found: {narration_path}")
        return

    entries = load_narration(narration_path)
    total   = len(entries)

    print(f"[vision] Processing {total} panels in {chapter_dir.name} …")

    for batch_start in range(0, total, batch_size):
        batch = entries[batch_start: batch_start + batch_size]
        dirty = False

        for entry in batch:
            img_path = _find_image(chapter_dir, entry.get("image", ""))
            if img_path is None:
                print(f"[vision] Image not found: {entry.get('image')} — skipping")
                continue

            if not _entry_needs_magi(entry):
                continue

            img = Image.open(img_path)
            _apply_magi(entry, img)
            dirty = True
            img.close()

        if dirty:
            save_narration(entries, narration_path)
            done = min(batch_start + batch_size, total)
            print(f"[vision] {done}/{total} panels processed, saved.")

    print(f"[vision] Done. {total} entries in {narration_path.name}")


# ---------------------------------------------------------------------------
# Auto mode: scan panels/ dir, build narration from scratch
# ---------------------------------------------------------------------------

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def process_panels(
    panels_dir: Path,
    narration_path: Path,
    batch_size: int = 4,
    force: bool = False,
) -> None:
    """Scan panels_dir for images, run MAGI, and write narration_path.

    Creates narration_path if it does not exist.  Skips entries that already
    have the requested fields (idempotent re-runs).
    """
    if not panels_dir.exists():
        print(f"[vision] Panels directory not found: {panels_dir}")
        return

    images = sorted(
        [f for f in panels_dir.iterdir() if f.is_file() and f.suffix.lower() in _IMAGE_EXTS],
        key=lambda p: numeric_sort_key(p.name),
    )
    if not images:
        print(f"[vision] No panel images found in {panels_dir}")
        return

    existing: dict[str, dict] = {}
    if narration_path.exists():
        for e in load_narration(narration_path):
            img = e.get("image", "")
            if img:
                if force:
                    for k in _MAGI_FIELDS:
                        e.pop(k, None)
                existing[img] = e
        action = "reset for reprocessing" if force else "loaded"
        print(f"[vision] {len(existing)} existing entries {action} from {narration_path.name}")

    entries: list[dict] = []
    for img_path in images:
        name = img_path.name
        entries.append(existing.get(name, {"image": name, "narration": ""}))

    total = len(entries)
    print(f"[vision] {total} panels found in {panels_dir.name}/")
    narration_path.parent.mkdir(parents=True, exist_ok=True)

    for batch_start in range(0, total, batch_size):
        batch_paths   = images[batch_start: batch_start + batch_size]
        batch_entries = entries[batch_start: batch_start + batch_size]
        dirty = False

        for img_path, entry in zip(batch_paths, batch_entries):
            if not _entry_needs_magi(entry):
                continue

            img = Image.open(img_path)
            _apply_magi(entry, img)
            dirty = True
            img.close()

        if dirty:
            save_narration(entries, narration_path)
            done = min(batch_start + batch_size, total)
            print(f"[vision] {done}/{total} panels processed → {narration_path.name}")

    print(f"[vision] Done. {total} entries saved to {narration_path}")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("chapter_dir", nargs="?", type=Path,
                   help="Path to chapter directory (omit when using --auto)")
    p.add_argument("--auto", action="store_true",
                   help="Read name/chapter from config.json; scan panels/ dir automatically")
    p.add_argument("--force", action="store_true",
                   help="Clear existing magi fields and reprocess all panels")
    p.add_argument("--batch", type=int, default=4, help="Panels per batch (default 4)")
    args = p.parse_args()
    if not args.auto and args.chapter_dir is None:
        p.error("chapter_dir is required unless --auto is specified")
    return args


def _resolve_auto_paths() -> tuple[Path, Path]:
    """Return (panels_dir, narration_json) from config.json."""
    dl      = load_download_config()
    name    = str(dl["name"])
    chapter = int(dl["chapter"])
    p_dir   = _panels_dir(name, chapter)
    n_path  = _narration_json(name, chapter)
    print(f"[vision] Auto mode — manga: {name!r}  chapter: {chapter:02d}")
    print(f"[vision] Panels : {p_dir}")
    print(f"[vision] Output : {n_path}")
    return p_dir, n_path


def main() -> None:
    args = _parse_args()

    if not _load_magi():
        print("[vision] MAGI v3 failed to load — aborting.")
        sys.exit(1)

    if args.auto:
        p_dir, n_path = _resolve_auto_paths()
        process_panels(p_dir, n_path, batch_size=args.batch, force=args.force)
    else:
        process_chapter(args.chapter_dir, batch_size=args.batch)


if __name__ == "__main__":
    main()
