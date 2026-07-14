"""Batch DeepSeek-OCR 2 over narration JSON files.

Runs inside the isolated deepseek-ocr2 tool environment. It writes an ``ocr``
field onto narration entries, preserving existing values unless ``--force`` is
passed.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from mangaeasy.ocr.ocr_clean import clean_ocr_text

MODEL_ID = "deepseek-ai/DeepSeek-OCR-2"
DEFAULT_PROMPT = "<image>\nFree OCR."
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run DeepSeek-OCR 2 over narration JSON panel entries.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--narration", type=Path, action="append",
                        help="Specific narration JSON file. Repeatable. If omitted, item folders are scanned.")
    parser.add_argument("--items", nargs="*", help="Item folder names or ranges, for example: 01 02 05-08.")
    parser.add_argument("--item-range", help="Convenience range, for example: 01-12.")
    parser.add_argument("--only-images", nargs="*", help="Only OCR these image filenames/stems.")
    parser.add_argument("--force", action="store_true", help="Replace existing ocr fields.")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--model", default=MODEL_ID, help="Local model dir or Hugging Face repo id.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT,
                        help="DeepSeek-OCR prompt. Use '<image>\\nFree OCR.' for plain text.")
    parser.add_argument("--base-size", type=int, default=1024)
    parser.add_argument("--image-size", type=int, default=768)
    parser.add_argument("--no-crop", action="store_true", help="Disable DeepSeek dynamic crop mode.")
    return parser.parse_args()


def _item_number(value: str) -> int:
    match = re.search(r"\d+", value)
    if not match:
        raise ValueError(f"Could not find a number in: {value}")
    return int(match.group(0))


def _expand_tokens(tokens: list[str] | None, width: int = 2) -> list[str] | None:
    if not tokens:
        return None
    out: list[str] = []
    for raw in tokens:
        for token in (part.strip() for part in raw.split(",")):
            if not token:
                continue
            match = re.fullmatch(r"(\d+)\s*(?:-|\.\.|:)\s*(\d+)", token)
            if match:
                start, end = int(match.group(1)), int(match.group(2))
                step = 1 if end >= start else -1
                out.extend(f"{number:0{width}d}" for number in range(start, end + step, step))
            elif token.isdigit():
                out.append(f"{int(token):0{width}d}")
            else:
                out.append(token)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in out:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _selected_items(items: list[str] | None, item_range: str | None) -> list[str] | None:
    tokens: list[str] = []
    if items:
        tokens.extend(items)
    if item_range:
        tokens.append(item_range)
    return _expand_tokens(tokens)


def _sort_key(path: Path) -> tuple[int, int, str]:
    has_number = any(ch.isdigit() for ch in path.name)
    number = _item_number(path.name) if has_number else 10**9
    return (0 if has_number else 1, number, path.name.lower())


def narration_paths(project_root: Path, args: argparse.Namespace) -> list[Path]:
    if args.narration:
        return [path.resolve() for path in args.narration]
    if not project_root.is_dir():
        return []

    selected = _selected_items(args.items, args.item_range)
    wanted_names = set(selected or [])
    wanted_numbers = {_item_number(name) for name in wanted_names if any(ch.isdigit() for ch in name)}
    paths: list[Path] = []
    for item_dir in sorted((p for p in project_root.iterdir() if p.is_dir()), key=_sort_key):
        if selected and item_dir.name not in wanted_names:
            if not any(ch.isdigit() for ch in item_dir.name) or _item_number(item_dir.name) not in wanted_numbers:
                continue
        for name in ("narration.json", f"narration_{item_dir.name}.json"):
            path = item_dir / name
            if path.is_file():
                paths.append(path.resolve())
                break
    return paths


def load_entries(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array or object")
    return [entry for entry in data if isinstance(entry, dict)]


def save_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def resolve_image(narration_path: Path, image_name: str) -> Path | None:
    raw = Path(image_name)
    candidates = [raw] if raw.is_absolute() else [
        narration_path.parent / raw,
        narration_path.parent / "panels" / raw,
        narration_path.parent / "download" / raw,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    stem = raw.stem
    for folder in (narration_path.parent, narration_path.parent / "panels", narration_path.parent / "download"):
        if not folder.is_dir():
            continue
        for candidate in folder.iterdir():
            if candidate.is_file() and candidate.stem == stem and candidate.suffix.lower() in IMAGE_EXTS:
                return candidate.resolve()
    return None


def normalize_ocr_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class DeepSeekOCR2:
    def __init__(self, model_source: str, device: str, prompt: str, base_size: int, image_size: int, crop: bool):
        import torch
        from transformers import AutoModel, AutoTokenizer

        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.prompt = prompt
        self.base_size = base_size
        self.image_size = image_size
        self.crop = crop
        print(f"[deepseek-ocr2] loading {model_source} on {device}", flush=True)

        self.tokenizer = AutoTokenizer.from_pretrained(model_source, trust_remote_code=True)
        kwargs: dict[str, Any] = {"trust_remote_code": True, "use_safetensors": True}
        if device == "cuda":
            kwargs["torch_dtype"] = torch.bfloat16
            kwargs["_attn_implementation"] = "flash_attention_2"
        try:
            self.model = AutoModel.from_pretrained(model_source, **kwargs)
        except Exception as exc:
            if kwargs.pop("_attn_implementation", None) is None:
                raise
            print(f"[deepseek-ocr2] flash-attention load failed ({exc}); retrying without it", flush=True)
            self.model = AutoModel.from_pretrained(model_source, **kwargs)
        self.model = self.model.eval()
        if device == "cuda":
            self.model = self.model.cuda().to(torch.bfloat16)
        else:
            self.model = self.model.to("cpu")
        print("[deepseek-ocr2] model ready", flush=True)

    def ocr(self, image: Path) -> str:
        import torch

        with tempfile.TemporaryDirectory(prefix="deepseek_ocr2_") as tmp:
            with torch.inference_mode():
                result = self.model.infer(
                    self.tokenizer,
                    prompt=self.prompt,
                    image_file=str(image),
                    output_path=tmp,
                    base_size=self.base_size,
                    image_size=self.image_size,
                    crop_mode=self.crop,
                    save_results=False,
                    eval_mode=True,
                )
        if isinstance(result, str):
            return normalize_ocr_text(result)
        return normalize_ocr_text(str(result or ""))


def process_file(path: Path, engine: DeepSeekOCR2, *, force: bool, only_images: set[str] | None) -> tuple[int, int, int]:
    entries = load_entries(path)
    pending: list[tuple[int, Path]] = []
    skipped = 0
    missing = 0
    for index, entry in enumerate(entries):
        image_name = str(entry.get("image") or "")
        if not image_name:
            skipped += 1
            continue
        image_key = Path(image_name).stem
        if only_images and image_name not in only_images and image_key not in only_images:
            skipped += 1
            continue
        # An empty OCR value is the valid result for a textless panel.  Only
        # entries without the key are pending unless the caller requests a
        # forced re-run.
        if "ocr" in entry and not force:
            skipped += 1
            continue
        image_path = resolve_image(path, image_name)
        if image_path is None:
            missing += 1
            print(f"[deepseek-ocr2] image not found for {path.name} entry {index + 1}: {image_name}", flush=True)
            continue
        pending.append((index, image_path))

    if not pending:
        print(f"[deepseek-ocr2] {path}: nothing to do ({skipped} skipped, {missing} missing)", flush=True)
        return 0, skipped, missing

    print(f"[deepseek-ocr2] {path}: {len(pending)} panel(s) pending", flush=True)
    written = 0
    for item_index, image_path in pending:
        try:
            entries[item_index]["ocr"] = clean_ocr_text(engine.ocr(image_path))
            written += 1
            print(f"[deepseek-ocr2] {image_path.name}: ok", flush=True)
        except Exception as exc:
            missing += 1
            print(f"[deepseek-ocr2] {image_path.name}: failed ({exc})", flush=True)
    if written:
        save_entries(path, entries)
        print(f"[deepseek-ocr2] saved {written} OCR field(s) -> {path}", flush=True)
    return written, skipped, missing


def main() -> int:
    args = parse_args()
    paths = narration_paths(args.project_root.resolve(), args)
    if not paths:
        print(f"[deepseek-ocr2] no narration JSON files found under {args.project_root}", flush=True)
        return 1

    only_images = set(args.only_images or []) or None
    engine = DeepSeekOCR2(
        args.model,
        args.device,
        args.prompt,
        args.base_size,
        args.image_size,
        not args.no_crop,
    )
    total_written = total_skipped = total_missing = 0
    for path in paths:
        written, skipped, missing = process_file(path, engine, force=args.force, only_images=only_images)
        total_written += written
        total_skipped += skipped
        total_missing += missing
    print(
        f"[deepseek-ocr2] done: written={total_written}, skipped={total_skipped}, problems={total_missing}",
        flush=True,
    )
    return 0 if total_missing == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
