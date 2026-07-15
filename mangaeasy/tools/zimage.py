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

from mangaeasy.brand import CLI_NAME
from mangaeasy.runtime import popen_kwargs
from mangaeasy.tools.external import python_command, resolve_tool_dir, tool_env
from mangaeasy.utils import emit_result


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog=f"{CLI_NAME} zimage",
        description="Generate images with Z-Image Turbo (text-to-image) inside its isolated tool env. "
                    f"Install first with: {CLI_NAME} install-tool z-image-turbo",
    )
    prompt_group = p.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt", help="Text prompt (English or Chinese; long, descriptive prompts work best).")
    prompt_group.add_argument("--prompt-file", type=Path,
                              help="Read the prompt from a UTF-8 file (for long prompts / shell-quoting safety).")
    prompt_group.add_argument("--batch-manifest", type=Path,
                              help="JSON array of {prompt|prompt_file, output, width, height, seed}; loads the model once.")
    p.add_argument("--output", type=Path,
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
    if not args.batch_manifest and args.output is None:
        p.error("--output is required for a single prompt")
    if args.batch_manifest and args.count != 1:
        p.error("--count is not used with --batch-manifest")
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
        print(f"        Install it with: {CLI_NAME} install-tool z-image-turbo", flush=True)
        print("        (~33 GB model download; runs on 8 GB+ NVIDIA GPUs via NF4 quantization)", flush=True)
        return 1
    adapter = tool_dir / "generate_zimage.py"
    if not adapter.is_file():
        print(f"[error] adapter missing: {adapter}", flush=True)
        print(f"        Re-run: {CLI_NAME} install-tool z-image-turbo --update", flush=True)
        return 1

    # Prefer the locally downloaded weights; fall back to the HF repo id
    # (weights then download into this install's redirected HF cache).
    model_dir = tool_dir / "model"
    model = str(model_dir) if (model_dir / "model_index.json").is_file() else "Tongyi-MAI/Z-Image-Turbo"

    cmd = [
        *python_command(tool_dir), str(adapter),
        "--width", str(args.width), "--height", str(args.height),
        "--steps", str(args.steps), "--count", str(args.count),
        "--strategy", args.strategy,
        "--model", model,
    ]
    if args.batch_manifest is not None:
        cmd += ["--batch-manifest", str(args.batch_manifest.resolve())]
    elif args.prompt is not None:
        cmd += ["--output", str(args.output.resolve())]
        cmd += ["--prompt", args.prompt]
    else:
        cmd += ["--output", str(args.output.resolve())]
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

    if args.batch_manifest:
        try:
            entries = __import__("json").loads(args.batch_manifest.read_text(encoding="utf-8"))
            outputs = [Path(entry["output"]).expanduser().resolve() for entry in entries]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"[error] invalid batch manifest: {exc}", flush=True)
            return 1
    else:
        outputs = expected_outputs(args.output.resolve(), args.count)
    missing = [str(o) for o in outputs if not o.is_file()]
    if missing:
        print(f"[error] generation reported success but outputs are missing: {missing}", flush=True)
        return 1
    emit_result(outputs=outputs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
