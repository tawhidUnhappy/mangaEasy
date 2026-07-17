"""mediaconductor.tools.gemma — run the local Gemma 4 LLM in its isolated env.

`mediaconductor llm` is the low-level entry point (one prompt, optional panel
images, optional JSON-schema-constrained output). The higher-level assist
commands (`crop-qa`, `characters --auto-draft`, `narrate-auto`) reuse
:func:`batch_generate`, which runs a whole manifest of requests through one
model load.

The runtime is a pinned llama.cpp `llama-server` installed by
`mediaconductor install-tool gemma-4` (see tools/install.py); the adapter
`run_gemma.py` inside the tool env owns the server lifecycle.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from mediaconductor import runtime
from mediaconductor.brand import CLI_NAME
from mediaconductor.tools.external import python_command, resolve_tool_dir, tool_env
from mediaconductor.utils import emit_result

MODEL_FILE = "gemma-4-E4B-it-Q4_0.gguf"
MMPROJ_FILE = "mmproj-gemma-4-E4B-it-Q8_0.gguf"

# Fallback for envs installed before the adapter shipped (same pattern as
# page-split's batch detector).
_PACKAGED_ADAPTER = Path(__file__).resolve().parents[1] / "assets" / "tools" / "run_gemma.py"


class GemmaUnavailable(RuntimeError):
    """gemma-4 is not installed/complete; the message says how to fix it."""


def resolve_gemma() -> dict:
    """Locate every piece the runner needs, or raise GemmaUnavailable."""
    from mediaconductor.tools.install import find_llama_server

    tool_dir = resolve_tool_dir("gemma-4", required=False)
    if tool_dir is None:
        raise GemmaUnavailable(
            f"gemma-4 is not installed. Install it with: {CLI_NAME} install-tool gemma-4 "
            "(~6 GB download; runs on CPU, offloads to GPU via Vulkan)"
        )
    model = tool_dir / "model" / MODEL_FILE
    mmproj = tool_dir / "model" / MMPROJ_FILE
    if not model.is_file():
        raise GemmaUnavailable(
            f"gemma-4 model weights are missing ({model}). "
            f"Re-run: {CLI_NAME} install-tool gemma-4 --update"
        )
    server = find_llama_server(tool_dir)
    if server is None:
        raise GemmaUnavailable(
            f"llama.cpp runtime is missing under {tool_dir / 'llama'}. "
            f"Re-run: {CLI_NAME} install-tool gemma-4 --update"
        )
    adapter = tool_dir / "run_gemma.py"
    if not adapter.is_file():
        adapter = _PACKAGED_ADAPTER
    return {
        "tool_dir": tool_dir,
        "model": model,
        "mmproj": mmproj if mmproj.is_file() else None,
        "server": server,
        "adapter": adapter,
    }


def gemma_ready() -> bool:
    try:
        resolve_gemma()
        return True
    except GemmaUnavailable:
        return False


def _adapter_command(resolved: dict, *, ctx_size: int, max_tokens: int,
                     temperature: float) -> list[str]:
    command = [
        *python_command(resolved["tool_dir"]), str(resolved["adapter"]),
        "--server-bin", str(resolved["server"]),
        "--model", str(resolved["model"]),
        "--ctx-size", str(ctx_size),
        "--max-tokens", str(max_tokens),
        "--temperature", str(temperature),
    ]
    if resolved["mmproj"] is not None:
        command += ["--mmproj", str(resolved["mmproj"])]
    return command


def batch_generate(
    requests: list[dict],
    *,
    work_dir: Path,
    ctx_size: int = 8192,
    max_tokens: int = 900,
    temperature: float = 0.4,
    log=print,
) -> list[str | None]:
    """Run *requests* through one Gemma server load; returns per-request text.

    Each request may carry prompt/system/images/json_schema (see run_gemma.py).
    Output paths are managed here, under ``work_dir``; a None in the returned
    list means that request failed (details are in the streamed adapter log).
    """
    resolved = resolve_gemma()
    work_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    output_paths: list[Path] = []
    for index, request in enumerate(requests, 1):
        output = work_dir / f"reply_{index:04d}.txt"
        output.unlink(missing_ok=True)
        entry = dict(request)
        entry["output"] = str(output.resolve())
        entry["images"] = [str(Path(p).resolve()) for p in request.get("images") or []]
        manifest.append(entry)
        output_paths.append(output)
    manifest_path = work_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                             encoding="utf-8")

    command = _adapter_command(resolved, ctx_size=ctx_size, max_tokens=max_tokens,
                               temperature=temperature)
    command += ["--batch-manifest", str(manifest_path.resolve()),
                "--server-log", str((work_dir / "llama-server.log").resolve())]
    env = tool_env()
    env["PYTHONUNBUFFERED"] = "1"
    log(f"[tool:gemma-4] {resolved['tool_dir']} ({len(requests)} request(s))")
    proc = runtime.popen(
        command, cwd=resolved["tool_dir"], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        log(line.rstrip())
    proc.wait()

    replies: list[str | None] = []
    for output in output_paths:
        replies.append(output.read_text(encoding="utf-8") if output.is_file() else None)
    return replies


def parse_json_reply(reply: str | None) -> dict | list | None:
    """Best-effort JSON extraction from a model reply (handles ``` fences)."""
    if not reply:
        return None
    text = reply.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except ValueError:
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        if start < 0:
            return None
        for end in range(len(text), start, -1):
            try:
                return json.loads(text[start:end])
            except ValueError:
                continue
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} llm",
        description="Run the local Gemma 4 LLM (text + images) inside its isolated env. "
                    f"Install first with: {CLI_NAME} install-tool gemma-4",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prompt", help="User prompt text.")
    mode.add_argument("--prompt-file", type=Path, help="Read the user prompt from a UTF-8 file.")
    mode.add_argument("--batch-manifest", type=Path,
                      help="JSON array of {prompt|prompt_file, system?, images?, json_schema?, "
                           "output}; loads the model once for all requests.")
    parser.add_argument("--system", help="Optional system prompt.")
    parser.add_argument("--system-file", type=Path)
    parser.add_argument("--image", action="append", default=[], type=Path,
                        help="Attach an image (repeatable; panels are downscaled automatically).")
    parser.add_argument("--json-schema-file", type=Path,
                        help="Constrain the reply to this JSON schema.")
    parser.add_argument("--output", type=Path, help="Write the reply to this file.")
    parser.add_argument("--ctx-size", type=int, default=8192)
    parser.add_argument("--max-tokens", type=int, default=900)
    parser.add_argument("--temperature", type=float, default=0.4)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        resolved = resolve_gemma()
    except GemmaUnavailable as exc:
        print(f"[error] {exc}", flush=True)
        return 1

    command = _adapter_command(resolved, ctx_size=args.ctx_size,
                               max_tokens=args.max_tokens, temperature=args.temperature)
    if args.batch_manifest is not None:
        command += ["--batch-manifest", str(args.batch_manifest.resolve())]
    else:
        if args.prompt is not None:
            command += ["--prompt", args.prompt]
        else:
            command += ["--prompt-file", str(args.prompt_file.resolve())]
        if args.system is not None:
            command += ["--system", args.system]
        elif args.system_file is not None:
            command += ["--system-file", str(args.system_file.resolve())]
        for image in args.image:
            command += ["--image", str(image.resolve())]
        if args.json_schema_file is not None:
            command += ["--json-schema-file", str(args.json_schema_file.resolve())]
        if args.output is not None:
            command += ["--output", str(args.output.resolve())]

    env = tool_env()
    env["PYTHONUNBUFFERED"] = "1"
    print(f"[tool:gemma-4] {resolved['tool_dir']}", flush=True)
    code = runtime.run(command, cwd=resolved["tool_dir"], env=env,
                       stderr=subprocess.STDOUT).returncode
    if code == 0:
        emit_result(command="llm", model=MODEL_FILE,
                    output=str(args.output) if args.output else None)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
