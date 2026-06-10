from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mangaeasy.tools.external import python_command, resolve_tool_dir, tool_env
from mangaeasy.video_pipeline.common import (
    DEFAULT_AUDIO_ROOT,
    DEFAULT_KOKORO_ROOT,
    DEFAULT_PROJECT_ROOT,
    DEFAULT_WORK_DIR,
    item_dirs,
    merge_item_selection,
    project_name,
)


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate one Kokoro WAV per narration item.")
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--kokoro-root", type=Path, default=DEFAULT_KOKORO_ROOT)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--items", nargs="*", help="Item folder names or ranges, for example: 01 02 05-08.")
    parser.add_argument("--item-range", help="Convenience range, for example: 01-12.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--voice", default="af_heart", help="Kokoro voice name or .pt voice tensor path.")
    parser.add_argument("--lang", default="a", help="Kokoro language code, for example a, b, en-us, fr-fr.")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--split-pattern", default=r"\n+")
    parser.add_argument("--prefetch", type=int, default=8)
    return parser.parse_args()


def item_audio_dir(args: argparse.Namespace, item_dir: Path) -> Path:
    return args.audio_root.resolve() / project_name(args.project_root, args.project_name) / item_dir.name


def load_narration(item_dir: Path) -> list[dict[str, str]]:
    path = item_dir / "narration.json"
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array.")
    return data


def validate_panel(item_dir: Path, image_name: str) -> Path:
    panel_path = item_dir / "panels" / image_name
    if panel_path.suffix.lower() not in IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported panel image extension: {panel_path}")
    if not panel_path.exists():
        raise FileNotFoundError(f"Missing panel: {panel_path}")
    return panel_path


def configure_fast_env(env: dict[str, str], kokoro_root: Path) -> dict[str, str]:
    env = dict(env)
    env.setdefault("CUDA_MODULE_LOADING", "LAZY")
    env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    env.setdefault("NVIDIA_TF32_OVERRIDE", "1")
    env.setdefault("OMP_NUM_THREADS", str(max(1, (os.cpu_count() or 4) - 1)))
    env.setdefault("TOKENIZERS_PARALLELISM", "false")
    env.setdefault("HF_XET_HIGH_PERFORMANCE", "1")

    espeak_root = Path("C:/Program Files/eSpeak NG")
    if espeak_root.exists():
        env["PATH"] = f"{espeak_root}{os.pathsep}{env.get('PATH', '')}"
        env.setdefault("ESPEAK_DATA_PATH", str(espeak_root / "espeak-ng-data"))

    env.setdefault("KOKORO_ROOT", str(kokoro_root))
    return env


def kokoro_python_command(kokoro_root: Path) -> list[str]:
    return python_command(kokoro_root)


def selected_kokoro_root(configured: Path) -> Path:
    if configured == DEFAULT_KOKORO_ROOT:
        resolved = resolve_tool_dir("kokoro-82m", "KOKORO_ROOT")
        assert resolved is not None
        return resolved
    return configured.expanduser().resolve()


def build_manifest(args: argparse.Namespace, selected_items: list[Path]) -> tuple[list[dict[str, str]], int]:
    manifest: list[dict[str, str]] = []
    skipped = 0

    for item_dir in selected_items:
        narration = load_narration(item_dir)
        audio_dir = item_audio_dir(args, item_dir)
        audio_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n[{item_dir.name}] {len(narration)} narration item(s)", flush=True)
        print(f"  audio -> {audio_dir}", flush=True)

        if args.prefetch > 0:
            with ThreadPoolExecutor(max_workers=1) as executor:
                list(
                    executor.map(
                        lambda item: validate_panel(item_dir, item.get("image", "")),
                        narration[: args.prefetch],
                    )
                )

        for idx, item in enumerate(narration, start=1):
            image_name = item.get("image")
            text = (item.get("narration") or item.get("text") or "").strip()
            if not image_name or not text:
                raise ValueError(f"Bad narration entry {idx} in {item_dir / 'narration.json'}")
            validate_panel(item_dir, image_name)
            output_path = audio_dir / f"{Path(image_name).stem}.wav"
            if output_path.exists() and not args.overwrite:
                skipped += 1
                continue
            manifest.append(
                {
                    "label": f"{item_dir.name}:{idx:03d}/{len(narration):03d}",
                    "text": text,
                    "output": str(output_path),
                }
            )

    return manifest, skipped


def write_manifest(args: argparse.Namespace, manifest: list[dict[str, str]]) -> Path:
    manifest_dir = args.work_dir.resolve() / "kokoro_manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    path = manifest_dir / "latest_manifest.json"
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return path


def run_kokoro_worker(args: argparse.Namespace, manifest_path: Path) -> None:
    script_dir = Path(__file__).resolve().parent
    worker = script_dir / "kokoro_batch_worker.py"
    kokoro_root = selected_kokoro_root(args.kokoro_root)
    command = [
        *kokoro_python_command(kokoro_root),
        str(worker),
        "--manifest",
        str(manifest_path),
        "--voice",
        args.voice,
        "--lang",
        args.lang,
        "--speed",
        str(args.speed),
        "--device",
        args.device,
        "--split-pattern",
        args.split_pattern,
    ]
    print(f"\nRunning Kokoro worker from: {kokoro_root}", flush=True)
    print(" ".join(command), flush=True)
    env = configure_fast_env(tool_env(), kokoro_root)
    subprocess.run(
        command,
        cwd=kokoro_root,
        env=env,
        check=True,
    )


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    if not project_root.exists():
        raise FileNotFoundError(f"Project root does not exist: {project_root}")

    selected_items = item_dirs(project_root, merge_item_selection(args.items, args.item_range))
    if not selected_items:
        raise FileNotFoundError(f"No item folders selected under {project_root}")

    if args.device == "cuda":
        print("Audio device: CUDA requested for Kokoro.", flush=True)
    print(f"Kokoro root: {selected_kokoro_root(args.kokoro_root)}", flush=True)

    manifest, skipped = build_manifest(args, selected_items)
    if not manifest:
        print(
            f"\nAudio already complete for {len(selected_items)} item folder(s); "
            f"skipped {skipped} existing file(s).",
            flush=True,
        )
        return 0

    manifest_path = write_manifest(args, manifest)
    print(f"\nQueued {len(manifest)} audio file(s); skipped {skipped} existing file(s).", flush=True)
    print(f"Manifest: {manifest_path}", flush=True)
    run_kokoro_worker(args, manifest_path)
    print(f"\nGenerated {len(manifest)} audio file(s) with Kokoro.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
