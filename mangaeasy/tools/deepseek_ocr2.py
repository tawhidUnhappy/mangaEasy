"""Run DeepSeek-OCR 2 inside its isolated tool environment."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from mangaeasy.brand import CLI_NAME
from mangaeasy.runtime import popen_kwargs
from mangaeasy.tools.external import python_command, resolve_tool_dir, tool_env


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} deepseek-ocr2",
        description="Run DeepSeek-OCR 2 over narration JSON files and write `ocr` fields.",
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--narration", type=Path, action="append",
                        help="Specific narration JSON file. Repeatable.")
    parser.add_argument("--items", nargs="*")
    parser.add_argument("--item-range")
    parser.add_argument("--only-images", nargs="*")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--prompt", default="<image>\nFree OCR.")
    parser.add_argument("--base-size", type=int, default=1024)
    parser.add_argument("--image-size", type=int, default=768)
    parser.add_argument("--no-crop", action="store_true")
    parser.add_argument("--deepseek-ocr2-root", type=Path, default=None,
                        help="Path to deepseek-ocr2 tool directory. Auto-detected if omitted.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tool_dir = (
        args.deepseek_ocr2_root.resolve()
        if args.deepseek_ocr2_root
        else resolve_tool_dir("deepseek-ocr2")
    )
    if tool_dir is None:
        print("[FATAL] Could not locate deepseek-ocr2 directory.", flush=True)
        return 1

    model_dir = tool_dir / "model"
    model = str(model_dir) if (model_dir / "config.json").is_file() else "deepseek-ai/DeepSeek-OCR-2"
    script = Path(__file__).resolve().parents[1] / "ocr" / "deepseek_ocr2_pipeline.py"
    cmd = [
        *python_command(tool_dir),
        str(script),
        "--project-root", str(args.project_root.resolve()),
        "--device", args.device,
        "--model", model,
        "--prompt", args.prompt,
        "--base-size", str(args.base_size),
        "--image-size", str(args.image_size),
    ]
    for narration in args.narration or []:
        cmd += ["--narration", str(narration.resolve())]
    if args.items:
        cmd += ["--items", *args.items]
    if args.item_range:
        cmd += ["--item-range", args.item_range]
    if args.only_images:
        cmd += ["--only-images", *args.only_images]
    if args.force:
        cmd.append("--force")
    if args.no_crop:
        cmd.append("--no-crop")

    env = tool_env()
    env["DEEPSEEK_OCR2_ROOT"] = str(tool_dir)
    env["DEEPSEEK_OCR2_DIR"] = str(tool_dir)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # The OCR worker runs under the tool's isolated Python environment, while
    # the worker script imports the small cleaning helpers from this checkout.
    # A source checkout is not installed into that isolated environment, so
    # make the repository package importable explicitly.  Preserve any
    # caller-provided PYTHONPATH for packaged/embedded deployments.
    package_root = str(Path(__file__).resolve().parents[2])
    inherited_pythonpath = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = (
        package_root
        if not inherited_pythonpath
        else package_root + os.pathsep + inherited_pythonpath
    )

    print(f"[tool:deepseek-ocr2] {tool_dir}", flush=True)
    return subprocess.run(
        cmd,
        cwd=tool_dir,
        env=env,
        stderr=subprocess.STDOUT,
        **popen_kwargs(),
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
