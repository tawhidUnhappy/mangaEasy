"""mangaeasy.tools.zimage — run Z-Image Turbo image generation in its isolated env.

Thin wrapper: validates arguments in the main env (so `--help` and usage
errors never need torch), then delegates to the `generate_zimage.py` adapter
inside the z-image-turbo tool env and emits `MANGAEASY_RESULT` with the
produced files on success.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from mangaeasy.runtime import popen_kwargs
from mangaeasy.tools.external import python_command, resolve_tool_dir, tool_env
from mangaeasy.utils import emit_result


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="mangaeasy zimage",
        description="Generate images with Z-Image Turbo (text-to-image) inside its isolated tool env. "
                    "Install first with: mangaeasy install-tool z-image-turbo",
    )
    p.add_argument("--prompt", help="Text prompt (English or Chinese; long, descriptive prompts work best).")
    p.add_argument("--prompt-file", type=Path,
                   help="Read the prompt from a UTF-8 file (for long prompts / shell-quoting safety).")
    p.add_argument("--output", type=Path, required=True,
                   help="Output PNG path. With --count > 1, files get _01.._NN suffixes.")
    p.add_argument("--width", type=int, default=1024, help="Image width (rounded down to a multiple of 16).")
    p.add_argument("--height", type=int, default=1024, help="Image height (rounded down to a multiple of 16).")
    p.add_argument("--steps", type=int, default=9,
                   help="Inference steps (8-9 is the Turbo sweet spot; more does not help).")
    p.add_argument("--seed", type=int, default=None, help="Base seed; --count increments it per image.")
    p.add_argument("--count", type=int, default=1, help="Number of variants to generate.")
    p.add_argument("--strategy", choices=("auto", "bf16", "nf4", "offload", "cpu"), default="auto",
                   help="VRAM strategy (auto picks per hardware; see docs/external-tools.md).")
    args = p.parse_args()
    if bool(args.prompt) == bool(args.prompt_file):
        p.error("pass exactly one of --prompt / --prompt-file")
    if args.count < 1:
        p.error("--count must be >= 1")
    return args


def expected_outputs(output: Path, count: int) -> list[Path]:
    """Mirror the adapter's output naming so we can verify what it produced."""
    if count == 1:
        return [output]
    suffix = output.suffix or ".png"
    return [output.with_name(f"{output.stem}_{i + 1:02d}{suffix}") for i in range(count)]


def main() -> int:
    args = parse_args()

    tool_dir = resolve_tool_dir("z-image-turbo", required=False)
    if tool_dir is None:
        print("[error] z-image-turbo is not installed.", flush=True)
        print("        Install it with: mangaeasy install-tool z-image-turbo", flush=True)
        print("        (~33 GB model download; runs on 8 GB+ NVIDIA GPUs via NF4 quantization)", flush=True)
        return 1
    adapter = tool_dir / "generate_zimage.py"
    if not adapter.is_file():
        print(f"[error] adapter missing: {adapter}", flush=True)
        print("        Re-run: mangaeasy install-tool z-image-turbo --update", flush=True)
        return 1

    # Prefer the locally downloaded weights; fall back to the HF repo id
    # (weights then download into this install's redirected HF cache).
    model_dir = tool_dir / "model"
    model = str(model_dir) if (model_dir / "model_index.json").is_file() else "Tongyi-MAI/Z-Image-Turbo"

    cmd = [
        *python_command(tool_dir), str(adapter),
        "--output", str(args.output.resolve()),
        "--width", str(args.width), "--height", str(args.height),
        "--steps", str(args.steps), "--count", str(args.count),
        "--strategy", args.strategy,
        "--model", model,
    ]
    if args.prompt is not None:
        cmd += ["--prompt", args.prompt]
    else:
        cmd += ["--prompt-file", str(args.prompt_file.resolve())]
    if args.seed is not None:
        cmd += ["--seed", str(args.seed)]

    env = tool_env()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    print(f"[tool:z-image-turbo] {tool_dir}", flush=True)
    rc = subprocess.run(
        cmd, cwd=tool_dir, env=env,
        stderr=subprocess.STDOUT,
        **popen_kwargs(),
    ).returncode
    if rc != 0:
        return rc

    outputs = expected_outputs(args.output.resolve(), args.count)
    missing = [str(o) for o in outputs if not o.is_file()]
    if missing:
        print(f"[error] generation reported success but outputs are missing: {missing}", flush=True)
        return 1
    emit_result(outputs=outputs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
