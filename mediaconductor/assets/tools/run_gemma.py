"""Adapter: run Gemma 4 (GGUF) through a pinned llama.cpp `llama-server`.

Copied into the gemma-4 tool env by `mediaconductor install-tool gemma-4` and
executed with that env's python. No mediaconductor imports — stdlib + Pillow
only (Pillow downscales panel images before they are base64-ed into vision
requests).

One server process is started per invocation (model loads once), then every
request — a single --prompt or a whole --batch-manifest — is sent to its
OpenAI-compatible /v1/chat/completions endpoint. The server is always torn
down on exit.

Batch manifest: a JSON array of request objects:

    [{"prompt": "..." | "prompt_file": "path",
      "system": "..." | "system_file": "path",     (optional)
      "images": ["panel1.jpg", ...],                (optional)
      "json_schema": {...},                         (optional; constrains output)
      "output": "reply.txt",                        (required: where to write)
      "max_tokens": 900, "temperature": 0.4}]       (optional overrides)

Exit codes: 0 = every request answered; 1 = server/model failure or any
request failed (partial outputs are kept so a re-run can resume).
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_MAX_TOKENS = 900
DEFAULT_TEMPERATURE = 0.4
# Panels keep their aspect ratio; width drives text legibility, the height cap
# keeps very tall webtoon panels from exploding the vision token budget.
IMAGE_MAX_WIDTH = 768
IMAGE_MAX_HEIGHT = 2048


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _encode_image(path: Path) -> str:
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        scale = min(1.0, IMAGE_MAX_WIDTH / im.width, IMAGE_MAX_HEIGHT / im.height)
        if scale < 1.0:
            im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))))
        buffer = io.BytesIO()
        im.save(buffer, "JPEG", quality=87)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _read_text_field(request: dict, key: str, base_dir: Path) -> str | None:
    if request.get(key) is not None:
        return str(request[key])
    file_key = f"{key}_file"
    if request.get(file_key):
        return Path(base_dir / str(request[file_key])).read_text(encoding="utf-8")
    return None


def _wait_healthy(port: int, proc: subprocess.Popen, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server exited during startup (code {proc.returncode})")
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1.0)
    raise RuntimeError(f"llama-server did not become healthy within {timeout:.0f}s")


def _chat(port: int, body: dict, timeout: float) -> str:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["choices"][0]["message"]["content"]


def _build_body(request: dict, base_dir: Path, args: argparse.Namespace) -> dict:
    prompt = _read_text_field(request, "prompt", base_dir)
    if not prompt:
        raise ValueError("request has no prompt/prompt_file")
    system = _read_text_field(request, "system", base_dir)
    images = [Path(base_dir / str(p)) for p in request.get("images") or []]

    if images:
        content: list | str = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": _encode_image(p)}} for p in images
        ]
    else:
        content = prompt
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": content}
    ]
    body = {
        "messages": messages,
        "max_tokens": int(request.get("max_tokens") or args.max_tokens),
        "temperature": float(
            args.temperature if request.get("temperature") is None else request["temperature"]
        ),
    }
    schema = request.get("json_schema")
    if schema:
        body["response_format"] = {"type": "json_object", "schema": schema}
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Gemma 4 via llama-server.")
    parser.add_argument("--server-bin", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--mmproj", type=Path, default=None,
                        help="Vision projector GGUF; required when any request has images.")
    parser.add_argument("--ctx-size", type=int, default=8192)
    parser.add_argument("--gpu-layers", type=int, default=99,
                        help="Layers to offload (ignored by CPU-only builds).")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--load-timeout", type=float, default=900.0)
    parser.add_argument("--request-timeout", type=float, default=1800.0)
    parser.add_argument("--server-log", type=Path, default=None)

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prompt")
    mode.add_argument("--prompt-file", type=Path)
    mode.add_argument("--batch-manifest", type=Path)
    parser.add_argument("--system")
    parser.add_argument("--system-file", type=Path)
    parser.add_argument("--image", action="append", default=[], type=Path)
    parser.add_argument("--json-schema-file", type=Path)
    parser.add_argument("--output", type=Path,
                        help="Write the reply here instead of printing it between markers.")
    args = parser.parse_args()

    base_dir = Path.cwd()
    if args.batch_manifest:
        base_dir = args.batch_manifest.resolve().parent
        requests = json.loads(args.batch_manifest.read_text(encoding="utf-8"))
        if not isinstance(requests, list) or not requests:
            print("[run_gemma] batch manifest must be a non-empty JSON array", flush=True)
            return 1
    else:
        single: dict = {}
        if args.prompt is not None:
            single["prompt"] = args.prompt
        else:
            single["prompt"] = args.prompt_file.read_text(encoding="utf-8")
        if args.system is not None:
            single["system"] = args.system
        elif args.system_file is not None:
            single["system"] = args.system_file.read_text(encoding="utf-8")
        if args.image:
            single["images"] = [str(p.resolve()) for p in args.image]
        if args.json_schema_file is not None:
            single["json_schema"] = json.loads(args.json_schema_file.read_text(encoding="utf-8"))
        if args.output is not None:
            single["output"] = str(args.output.resolve())
        requests = [single]

    needs_vision = any(request.get("images") for request in requests)
    if needs_vision and args.mmproj is None:
        print("[run_gemma] requests contain images but no --mmproj was provided", flush=True)
        return 1

    port = _free_port()
    command = [
        str(args.server_bin), "-m", str(args.model),
        "--host", "127.0.0.1", "--port", str(port),
        "--ctx-size", str(args.ctx_size), "-ngl", str(args.gpu_layers),
        "--jinja",
    ]
    if needs_vision:
        command += ["--mmproj", str(args.mmproj)]

    popen_kwargs: dict = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    log_handle = None
    if args.server_log is not None:
        args.server_log.parent.mkdir(parents=True, exist_ok=True)
        log_handle = args.server_log.open("wb")
    print(f"[run_gemma] starting llama-server (port {port}, "
          f"{'vision' if needs_vision else 'text-only'})", flush=True)
    proc = subprocess.Popen(
        command,
        stdout=log_handle or subprocess.DEVNULL,
        stderr=log_handle or subprocess.DEVNULL,
        cwd=str(args.server_bin.parent),
        **popen_kwargs,
    )
    failures = 0
    try:
        _wait_healthy(port, proc, args.load_timeout)
        print("[run_gemma] model loaded", flush=True)
        for index, request in enumerate(requests, 1):
            print(f"MEDIACONDUCTOR_PROGRESS {index}/{len(requests)} llm", flush=True)
            try:
                body = _build_body(request, base_dir, args)
                reply = _chat(port, body, args.request_timeout)
            except Exception as exc:  # keep going; partial batches are resumable
                failures += 1
                print(f"[run_gemma] request {index} failed: {exc}", flush=True)
                continue
            output = request.get("output")
            if output:
                destination = Path(base_dir / str(output))
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(reply, encoding="utf-8")
            else:
                print("GEMMA_OUTPUT_BEGIN", flush=True)
                print(reply, flush=True)
                print("GEMMA_OUTPUT_END", flush=True)
    except RuntimeError as exc:
        print(f"[run_gemma] {exc}", flush=True)
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        if log_handle is not None:
            log_handle.close()
    if failures:
        print(f"[run_gemma] {failures}/{len(requests)} request(s) failed", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
