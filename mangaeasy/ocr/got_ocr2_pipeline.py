#!/usr/bin/env python3
"""Batch GOT-OCR 2.0 panel OCR for narration JSON files.

IMPORTANT: This module is intended to run inside the isolated got-ocr2 uv
environment. The public entry point is:

    mangaeasy got-ocr2

It adds an `ocr` field to each narration entry, preserving existing values
unless --force is passed.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import traceback
from pathlib import Path

_HERE = Path(__file__).resolve()
_PACKAGE_PARENT = _HERE.parent.parent.parent
if str(_PACKAGE_PARENT) not in sys.path:
    sys.path.append(str(_PACKAGE_PARENT))

from mangaeasy.narration import load_narration, save_narration
from mangaeasy.utils import numeric_sort_key
from mangaeasy.video_pipeline.common import item_dirs, merge_item_selection

MODEL_ID = "stepfun-ai/GOT-OCR-2.0-hf"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
SKIP_SCAN_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    ".cache",
    ".hf_cache",
    "audio",
    "output",
    "work",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run GOT-OCR 2.0 over narration JSON panel entries and write an `ocr` field."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd(),
                        help="Project or manga folder to scan. Defaults to the current directory.")
    parser.add_argument("--narration", nargs="*", type=Path,
                        help="Specific narration JSON file(s) to process.")
    parser.add_argument("--items", nargs="*",
                        help="Item folders to process when using the item pipeline.")
    parser.add_argument("--item-range",
                        help="Item range such as 01-24 when using the item pipeline.")
    parser.add_argument("--force", action="store_true",
                        help="Replace existing `ocr` fields.")
    parser.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="auto",
                        help="Inference device. Default: auto.")
    parser.add_argument("--batch-size", type=int, default=1,
                        help="Panels per generation batch. Default: 1.")
    parser.add_argument("--max-new-tokens", type=int, default=1024,
                        help="Maximum OCR tokens per panel. Default: 1024.")
    parser.add_argument("--formatted", action="store_true",
                        help="Use GOT-OCR formatted mode. Plain mode is the mangaEasy default for dialogue accuracy.")
    parser.add_argument("--plain", action="store_true",
                        help="Compatibility flag; plain OCR mode is already the default.")
    parser.add_argument("--no-readability-breaks", action="store_true",
                        help="Do not add readability line breaks after plain OCR.")
    parser.add_argument("--model", default=None,
                        help="Model id or local model directory. Defaults to the installed HF model.")
    parser.add_argument("--hf-cache", type=Path, default=None,
                        help="Hugging Face cache directory. Defaults to <project-root>/.hf_cache.")
    parser.add_argument("--only-images", nargs="*", default=None,
                        help="Only process entries whose image filename is listed.")
    return parser.parse_args()


def configure_hf_cache(project_root: Path, hf_cache: Path | None) -> None:
    cache = (hf_cache or (project_root / ".hf_cache")).resolve()
    if hf_cache is not None:
        os.environ["HF_HOME"] = str(cache)
        os.environ["HF_HUB_CACHE"] = str(cache / "hub")
    else:
        os.environ.setdefault("HF_HOME", str(cache))
        os.environ.setdefault("HF_HUB_CACHE", str(cache / "hub"))
    os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def default_model_source() -> str:
    configured = os.environ.get("GOT_OCR2_MODEL")
    if configured:
        return configured
    tool_root = (
        os.environ.get("GOT_OCR2_ROOT")
        or os.environ.get("GOT_OCR2_DIR")
        or os.environ.get("GOT_OCR_ROOT")
    )
    if tool_root:
        local_model = Path(tool_root).expanduser().resolve() / "model"
        if (local_model / "config.json").exists():
            return str(local_model)
    return MODEL_ID


def is_narration_file(path: Path) -> bool:
    name = path.name
    return name == "narration.json" or (name.startswith("narration_") and name.endswith(".json"))


def discover_narration_paths(args: argparse.Namespace) -> list[Path]:
    if args.narration:
        paths = [p.expanduser().resolve() for p in args.narration]
    elif args.items or args.item_range:
        selected = item_dirs(args.project_root, merge_item_selection(args.items, args.item_range))
        paths = [(item / "narration.json").resolve() for item in selected if (item / "narration.json").exists()]
    else:
        paths = []
        for current, dirnames, filenames in os.walk(args.project_root):
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_SCAN_DIRS and not d.startswith(".")
            ]
            folder = Path(current)
            for filename in filenames:
                candidate = folder / filename
                if is_narration_file(candidate):
                    paths.append(candidate.resolve())

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return sorted(unique, key=lambda p: (str(p.parent).lower(), numeric_sort_key(p.name), p.name.lower()))


def find_image(narration_path: Path, image_name: str) -> Path | None:
    if not image_name:
        return None
    raw = Path(image_name)
    if raw.is_absolute() and raw.is_file():
        return raw
    base = narration_path.parent
    candidates = [
        base / raw,
        base / "panels" / raw,
        base / "panels_filename" / raw,
        base / "download" / raw,
    ]
    for candidate in candidates:
        if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTS:
            return candidate
    return None


def resolve_device(pref: str):
    import torch

    from mangaeasy.tools.external import resolve_device as _resolve_device

    if pref == "cpu":
        return "cpu"
    if pref == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("--device cuda was requested, but torch cannot see CUDA.")
        return "cuda"
    if pref == "mps":
        mps = getattr(torch.backends, "mps", None)
        if mps is None or not mps.is_available():
            raise RuntimeError("--device mps was requested, but torch cannot see an Apple Silicon GPU.")
        return "mps"
    return _resolve_device("auto")


class GotOcr2Engine:
    def __init__(
        self,
        model_source: str,
        device_pref: str,
        max_new_tokens: int,
        *,
        formatted: bool,
        readability_breaks: bool,
    ) -> None:
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        self.torch = torch
        self.max_new_tokens = max_new_tokens
        self.formatted = formatted
        self.readability_breaks = readability_breaks
        self.device = resolve_device(device_pref)
        load_kwargs = {}
        if self.device == "cuda":
            load_kwargs["device_map"] = "auto"
            load_kwargs["torch_dtype"] = torch.float16
        else:
            load_kwargs["torch_dtype"] = torch.float32

        mode = "formatted" if self.formatted else "plain"
        print(f"[got-ocr2] loading {model_source} on {self.device} ({mode})", flush=True)
        self.processor = AutoProcessor.from_pretrained(model_source, use_fast=True)
        self.model = AutoModelForImageTextToText.from_pretrained(model_source, **load_kwargs)
        if self.device == "cpu":
            self.model = self.model.to("cpu")
        self.model.eval()
        print("[got-ocr2] model ready", flush=True)

    def input_device(self):
        model_device = getattr(self.model, "device", None)
        if model_device is not None:
            return model_device
        return self.torch.device("cuda" if self.device == "cuda" else "cpu")

    def _generate(self, inputs):
        kwargs = dict(
            **inputs,
            do_sample=False,
            tokenizer=self.processor.tokenizer,
            stop_strings="<|im_end|>",
            max_new_tokens=self.max_new_tokens,
        )
        try:
            return self.model.generate(**kwargs)
        except (TypeError, ValueError) as exc:
            if "stop_strings" not in str(exc):
                raise
            kwargs.pop("stop_strings", None)
            return self.model.generate(**kwargs)

    def ocr_batch(self, image_paths: list[Path]) -> list[str]:
        from PIL import Image

        images = [Image.open(path).convert("RGB") for path in image_paths]
        try:
            inputs = self.processor(images, return_tensors="pt", format=self.formatted).to(self.input_device())
            start = inputs["input_ids"].shape[1]
            with self.torch.inference_mode():
                generated = self._generate(inputs)
            outputs = self.processor.batch_decode(
                generated[:, start:],
                skip_special_tokens=True,
            )
            return [normalize_ocr_text(text, readability_breaks=self.readability_breaks) for text in outputs]
        finally:
            for image in images:
                image.close()


def add_readability_breaks(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", "\n", text)
    text = re.sub(r"([.!?。！？])\s+(?=([\"'“‘(\[])?[A-Z0-9])", r"\1\n", text)
    return text


def normalize_ocr_text(text: str, *, readability_breaks: bool = True) -> str:
    text = text.replace("<|im_end|>", "").replace("\r\n", "\n").replace("\r", "\n")
    if readability_breaks:
        text = add_readability_breaks(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunks(items: list[tuple[int, Path]], size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def process_narration(
    path: Path,
    engine: GotOcr2Engine,
    *,
    force: bool,
    batch_size: int,
    only_images: set[str] | None = None,
) -> tuple[int, int, int]:
    try:
        entries = load_narration(path)
    except Exception as exc:
        print(f"[got-ocr2] could not read {path}: {exc}", flush=True)
        return (0, 0, 1)

    if not entries:
        print(f"[got-ocr2] empty narration file: {path}", flush=True)
        return (0, 0, 0)

    pending: list[tuple[int, Path]] = []
    skipped = 0
    missing = 0
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            skipped += 1
            continue
        image_name = str(entry.get("image") or "")
        if only_images is not None and image_name not in only_images:
            skipped += 1
            continue
        if "ocr" in entry and not force:
            skipped += 1
            continue
        image_path = find_image(path, image_name)
        if image_path is None:
            print(f"[got-ocr2] image not found for {path.name} entry {index + 1}: {entry.get('image')}", flush=True)
            missing += 1
            continue
        pending.append((index, image_path))

    if not pending:
        print(f"[got-ocr2] {path}: nothing to do ({skipped} skipped, {missing} missing)", flush=True)
        return (0, skipped, missing)

    print(f"[got-ocr2] {path}: {len(pending)} panel(s) pending", flush=True)
    written = 0
    errors = 0
    safe_batch_size = max(1, batch_size)
    for batch in chunks(pending, safe_batch_size):
        batch_paths = [image_path for _, image_path in batch]
        try:
            texts = engine.ocr_batch(batch_paths)
        except Exception as exc:
            print(f"[got-ocr2] OCR batch failed in {path}: {exc}", flush=True)
            traceback.print_exc()
            errors += len(batch)
            continue
        for (index, image_path), text in zip(batch, texts):
            entries[index]["ocr"] = text
            written += 1
            print(f"  [{written}/{len(pending)}] {image_path.name}: {text[:80]!r}", flush=True)
        save_narration(entries, path)

    print(f"[got-ocr2] saved {written} OCR field(s) -> {path}", flush=True)
    return (written, skipped, missing + errors)


def main() -> int:
    args = parse_args()
    args.project_root = args.project_root.expanduser().resolve()
    configure_hf_cache(args.project_root, args.hf_cache)

    paths = discover_narration_paths(args)
    if not paths:
        print(f"[got-ocr2] no narration JSON files found under {args.project_root}", flush=True)
        return 1

    print(f"[got-ocr2] found {len(paths)} narration file(s)", flush=True)
    model_source = args.model or default_model_source()
    try:
        engine = GotOcr2Engine(
            model_source,
            args.device,
            max(1, args.max_new_tokens),
            formatted=bool(args.formatted and not args.plain),
            readability_breaks=not args.no_readability_breaks,
        )
    except Exception as exc:
        print(f"[got-ocr2] failed to load model: {exc}", flush=True)
        traceback.print_exc()
        return 1

    total_written = 0
    total_skipped = 0
    total_problems = 0
    only_images = set(args.only_images) if args.only_images else None
    for path in paths:
        written, skipped, problems = process_narration(
            path,
            engine,
            force=args.force,
            batch_size=args.batch_size,
            only_images=only_images,
        )
        total_written += written
        total_skipped += skipped
        total_problems += problems

    print(
        f"[got-ocr2] done: written={total_written}, skipped={total_skipped}, problems={total_problems}",
        flush=True,
    )
    return 1 if total_written == 0 and total_problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
