"""mangaeasy.tools.install — provision the external AI tool environments.

These heavy tools (IndexTTS, MAGI v3, DeepSeek-OCR 2, Kokoro, Z-Image Turbo)
are deliberately kept in their own isolated ``uv`` environments instead of
being dependencies of mangaeasy, so their conflicting torch/transformers stacks
never clash with the core install. This module clones / sets them up into the
managed tools dir
(``<app_root>/.mangaeasy/tools`` by default — self-contained, removed along
with the install/repo folder).

Used by the ``mangaeasy install-tool`` and ``mangaeasy doctor`` subcommands, and
by the control-center app, which reuses :func:`install_tool` with a streaming
log callback.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from mangaeasy.runtime import popen_kwargs
from mangaeasy.tools.external import (
    python_command,
    resolve_tool_dir,
    tool_env,
    tools_home,
)
from mangaeasy.tools.hardware import (
    default_torch_build,
    detect_gpu,
    find_nvidia_smi,
    has_nvidia_gpu,
    nvidia_gpu_name,
    which,
)

LogFn = Callable[[str], None]
ASSETS_TOOLS = Path(__file__).resolve().parents[1] / "assets" / "tools"


class InstallError(RuntimeError):
    """Raised when a provisioning step fails; carries a human-readable message."""


# ── Manifest ────────────────────────────────────────────────────────────────


@dataclass
class ToolSpec:
    key: str
    title: str
    kind: str  # "uv_project" (clone + uv sync) | "managed_env" (we author the env)
    git_url: str | None
    ref: str | None = None
    model_repo: str | None = None
    model_subdir: str | None = None
    adapter: str | None = None          # asset filename to copy into the tool dir
    env_deps: list[str] = field(default_factory=list)  # for managed_env
    exclude_extras: list[str] = field(default_factory=list)  # extras uv sync must skip
    verify_import: str | None = None    # module to import-check inside the env
    needs_gpu: bool = False
    notes: str = ""


TOOLS: dict[str, ToolSpec] = {
    "index-tts": ToolSpec(
        key="index-tts",
        title="IndexTTS 2",
        kind="uv_project",
        git_url="https://github.com/index-tts/index-tts",
        model_repo="IndexTeam/IndexTTS-2",
        model_subdir="checkpoints",
        # DeepSpeed is a training accelerator, unused for inference, and its
        # native build fails on most machines (needs the system CUDA toolkit
        # to exactly match torch's, plus aio/cufile libs Windows lacks).
        exclude_extras=["deepspeed"],
        needs_gpu=True,
        notes="High-quality voice-cloning TTS; the default engine for `mangaeasy video` on NVIDIA GPU machines. ~5.9 GB model download from Hugging Face (config, gpt.pth, s2mel.pth, bpe.model).",
    ),
    "faster-whisper": ToolSpec(
        key="faster-whisper",
        title="Faster Whisper (transcription)",
        kind="managed_env",
        git_url=None,
        env_deps=[
            "faster-whisper>=1.2.1",
            "huggingface-hub>=0.21",
            # onnxruntime 1.24+ dropped Python 3.10 support; pin to keep it working
            "onnxruntime>=1.14,<1.24",
        ],
        verify_import="faster_whisper",
        needs_gpu=False,
        notes="Optional: fast Whisper audio transcription. Runs on CPU; ctranslate2 auto-uses CUDA if available. Models download from Hugging Face on first use.",
    ),
    "magi-v3": ToolSpec(
        key="magi-v3",
        title="MAGI v3 (panel detection)",
        kind="managed_env",
        git_url="https://github.com/ragavsachdeva/magi",
        adapter="detect_magi.py",
        env_deps=[
            "torch>=2.5.0",
            "torchvision>=0.20.0",
            "transformers>=4.41,<5.0",
            "accelerate>=1.12.0",
            "safetensors>=0.4.0",
            "timm>=0.9.0",
            "einops>=0.8.2",
            "pillow>=10.0.0",
            "numpy>=1.24.0",
        ],
        verify_import="transformers",
        needs_gpu=True,
        notes="Detects manga panels. The magiv3 model + code download from Hugging Face on first run.",
    ),
    "deepseek-ocr2": ToolSpec(
        key="deepseek-ocr2",
        title="DeepSeek-OCR 2",
        kind="managed_env",
        git_url="https://github.com/deepseek-ai/DeepSeek-OCR-2",
        model_repo="deepseek-ai/DeepSeek-OCR-2",
        model_subdir="model",
        env_deps=[
            "torch>=2.6.0",
            "torchvision>=0.21.0",
            "transformers==4.46.3",
            "tokenizers>=0.20.3",
            "accelerate>=1.0.0",
            "safetensors>=0.4.0",
            "pillow>=10.0.0",
            "numpy>=1.24.0",
            "einops>=0.8.0",
            "addict>=2.4.0",
            "easydict>=1.13",
            "matplotlib>=3.8.0",
            "tqdm>=4.66.0",
        ],
        verify_import="transformers",
        needs_gpu=True,
        notes="DeepSeek-OCR 2 document/panel OCR. Installs a managed Transformers env and downloads the Apache-2.0 deepseek-ai/DeepSeek-OCR-2 model from Hugging Face.",
    ),
    "kokoro-82m": ToolSpec(
        key="kokoro-82m",
        title="Kokoro 82M (default TTS)",
        kind="managed_env",
        git_url="https://github.com/hexgrad/kokoro",  # pip-installable; cloned only with --clone
        env_deps=[
            "kokoro>=0.9",
            "torch>=2.5.0",
            "soundfile>=0.12",
            "numpy>=1.24.0",
        ],
        verify_import="kokoro",
        needs_gpu=False,
        notes="Light TTS (voice af_heart); the default engine for `mangaeasy video` on machines without an NVIDIA GPU. Model downloads from Hugging Face on first run.",
    ),
    "z-image-turbo": ToolSpec(
        key="z-image-turbo",
        title="Z-Image Turbo (image generation)",
        kind="managed_env",
        git_url=None,
        model_repo="Tongyi-MAI/Z-Image-Turbo",
        model_subdir="model",
        adapter="generate_zimage.py",
        env_deps=[
            "torch>=2.5.0",
            "diffusers>=0.36.0",       # ZImagePipeline landed in 0.36.0
            "transformers>=4.51.0",    # text encoder is Qwen3 (added in 4.51)
            "accelerate>=1.0.0",
            "safetensors>=0.4.0",
            "pillow>=10.0.0",
            "numpy>=1.24.0",
            # NF4 4-bit quantization — how the 6B model fits consumer GPUs
            # (8-12 GB). No macOS builds; Apple Silicon runs bf16 on MPS instead.
            "bitsandbytes>=0.45 ; sys_platform != 'darwin'",
        ],
        verify_import="diffusers",
        needs_gpu=True,
        notes="Text-to-image generation (thumbnails, backgrounds) with Alibaba's Z-Image Turbo, Apache-2.0. ~33 GB model download from Hugging Face. Runs on 8-16 GB NVIDIA GPUs via automatic NF4 quantization; bf16 on 16 GB+ GPUs and Apple Silicon.",
    ),
}


# ── Shell helpers ─────────────────────────────────────────────────────────────


# Strips ANSI colour/cursor codes. \r is kept and handled separately in
# _run_pty_win32 to collapse progress-bar frames into a single final line.
_ANSI_RE = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _run_pty_win32(cmd: list[str], log: LogFn, cwd: Path | None = None, env: dict | None = None) -> None:
    """Run *cmd* inside a Windows ConPTY so the child flushes every line.

    \r (bare carriage return) means "overwrite the current line" — the same
    semantics a real terminal uses for tqdm/progress bars.  We track
    *current_line* and reset it on \r, so only the final state of each
    progress bar (the 100 % line that ends with \n) is ever logged.
    """
    from winpty import PtyProcess  # type: ignore[import-untyped]  # pywinpty

    proc = PtyProcess.spawn(cmd, cwd=str(cwd) if cwd else None, env=env, dimensions=(50, 300))
    current_line = ""

    def _process_text(text: str) -> None:
        nonlocal current_line
        i = 0
        while i < len(text):
            c = text[i]
            if c == "\r":
                if i + 1 < len(text) and text[i + 1] == "\n":
                    # CRLF — treat as a completed line then advance past \n
                    stripped = current_line.rstrip()
                    if stripped:
                        log(stripped)
                    current_line = ""
                    i += 2
                    continue
                else:
                    # Bare \r — overwrite: discard current line content
                    current_line = ""
            elif c == "\n":
                stripped = current_line.rstrip()
                if stripped:
                    log(stripped)
                current_line = ""
            else:
                current_line += c
            i += 1

    while proc.isalive():
        try:
            chunk = proc.read(4096)
        except Exception:
            break
        if not chunk:
            continue
        _process_text(_strip_ansi(chunk))
    # drain any tail after process exits
    try:
        while True:
            chunk = proc.read(4096)
            if not chunk:
                break
            _process_text(_strip_ansi(chunk))
    except Exception:
        pass
    if current_line.strip():
        log(current_line.strip())
    proc.wait()
    rc = proc.exitstatus or 0
    if rc != 0:
        raise InstallError(f"command failed (exit {rc}): {subprocess.list2cmdline(cmd)}")


def _run_pipe(cmd: list[str], log: LogFn, cwd: Path | None = None, env: dict | None = None) -> None:
    """Run *cmd* with stdout/stderr merged into a pipe (output arrives in ~4 KB bursts)."""
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **popen_kwargs(),
        )
    except FileNotFoundError as exc:
        raise InstallError(f"command not found: {cmd[0]} ({exc})") from exc
    assert proc.stdout is not None
    for line in proc.stdout:
        log(line.rstrip("\n"))
    code = proc.wait()
    if code != 0:
        raise InstallError(f"command failed (exit {code}): {' '.join(cmd)}")


def _run(cmd: list[str], log: LogFn, cwd: Path | None = None, env: dict | None = None) -> None:
    # Every install-tool subprocess (git clone, uv sync, hf download, …) runs
    # under tool_env() by default so its caches (UV_CACHE_DIR, HF_HOME, …)
    # always land under this install's own .mangaeasy/ dir, never the
    # system-wide default — explicit `env=` callers (e.g. one-off env tweaks
    # that already merged tool_env() themselves) are left untouched.
    if env is None:
        env = tool_env()
    log(f"$ {' '.join(str(c) for c in cmd)}")
    # On Windows use a ConPTY (pywinpty) so child processes see a real terminal
    # and flush output line-by-line.  Falls back to a regular pipe if pywinpty
    # is not installed yet (first run before the dep is available).
    if sys.platform == "win32":
        try:
            _run_pty_win32(cmd, log, cwd=cwd, env=env)
            return
        except ImportError:
            pass  # pywinpty not installed — fall through to pipe mode
        except InstallError:
            raise  # command itself failed — don't retry, propagate immediately
        except Exception as exc:
            log(f"[warn] PTY launch failed ({exc}), retrying with pipe")
    _run_pipe(cmd, log, cwd=cwd, env=env)


def _which(exe: str) -> str | None:
    return which(exe)


def _require(executables: list[str], log: LogFn) -> None:
    hints = {
        "git": "Install Git: https://git-scm.com/downloads",
        "uv": "Install uv: https://docs.astral.sh/uv/getting-started/installation/",
        "uvx": "uvx ships with uv: https://docs.astral.sh/uv/",
    }
    missing = [exe for exe in executables if not _which(exe)]
    if missing:
        lines = "\n".join(f"  - {m}: {hints.get(m, 'not found on PATH')}" for m in missing)
        raise InstallError(f"Missing required tools on PATH:\n{lines}")


def _git_lfs_ok() -> bool:
    if not _which("git"):
        return False
    try:
        return subprocess.run(["git", "lfs", "version"], capture_output=True, **popen_kwargs()).returncode == 0
    except Exception:
        return False


def _find_nvidia_smi() -> str | None:
    return find_nvidia_smi()


def _has_gpu() -> bool:
    return has_nvidia_gpu()


def _nvidia_gpu_name() -> str | None:
    return nvidia_gpu_name()


def default_gpu_mode() -> str:
    return default_torch_build()


def _torch_index_url(mode: str) -> str | None:
    if mode == "cuda":
        return "https://download.pytorch.org/whl/cu128"
    if mode == "cpu" and sys.platform == "linux":
        # Linux PyPI torch bundles CUDA libs; the cpu index is far smaller.
        return "https://download.pytorch.org/whl/cpu"
    return None  # plain PyPI: CPU build on Windows, CPU/MPS on macOS


# ── Install steps ──────────────────────────────────────────────────────────────


def _clone_or_update(git_url: str, dest: Path, ref: str | None, log: LogFn,
                     skip_lfs_smudge: bool = False) -> None:
    if (dest / ".git").exists():
        log(f"Updating existing clone at {dest}")
        _run(["git", "-C", str(dest), "fetch", "--all", "--tags"], log)
        if ref:
            _run(["git", "-C", str(dest), "checkout", ref], log)
            _run(["git", "-C", str(dest), "pull", "--ff-only"], log)
    else:
        clone_env = {**tool_env(), "GIT_LFS_SKIP_SMUDGE": "1"} if skip_lfs_smudge else None
        _run(["git", "clone", git_url, str(dest)], log, env=clone_env)
        if ref:
            _run(["git", "-C", str(dest), "checkout", ref], log)


def _download_model(spec: ToolSpec, dest: Path, log: LogFn) -> None:
    if not spec.model_repo:
        return
    target = dest / (spec.model_subdir or "checkpoints")
    log(f"Downloading model {spec.model_repo} -> {target}")
    _require(["uvx"], log)
    # PYTHONUTF8=1 prevents Windows charmap errors when hf CLI prints Unicode
    # success symbols (e.g. ✓ U+2713) to a pipe that uses a legacy code page.
    env = {**tool_env(), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    # Plain huggingface-hub: since 1.x the `hf` CLI and Xet transfer are part
    # of the base package — the old `[cli,hf_xet]` extras no longer exist and
    # only produced install warnings.
    _run(
        [
            "uvx", "--from", "huggingface-hub",
            "hf", "download", spec.model_repo, "--local-dir", str(target),
        ],
        log,
        env=env,
    )


def _verify_tool_python(dest: Path, import_check: str, log: LogFn) -> None:
    cmd = [*python_command(dest), "-c", f"import {import_check}; print('ok: {import_check}')"]
    try:
        _run(cmd, log, cwd=dest, env=tool_env())
    except InstallError as exc:
        log(f"[warn] verify import '{import_check}' failed: {exc}")


def _install_uv_project(
    spec: ToolSpec, dest: Path, ref: str | None, skip_model: bool, log: LogFn,
    gpu_mode: str = "cpu",
) -> None:
    if not spec.git_url:
        raise InstallError(
            f"No git URL is configured for '{spec.key}'. Edit TOOLS['{spec.key}'].git_url "
            f"in mangaeasy/tools/install.py (or install it manually)."
        )
    _require(["git", "uv"], log)
    # Skip LFS smudge during clone so GitHub LFS bandwidth is never consumed.
    # Any large model files are fetched from Hugging Face by _download_model().
    _clone_or_update(spec.git_url, dest, ref, log, skip_lfs_smudge=True)

    sync_cmd = ["uv", "sync", "--all-extras"]
    for extra in spec.exclude_extras:
        log(f"[info] skipping optional extra '{extra}' (not needed for inference)")
        sync_cmd += ["--no-extra", extra]
    _run(sync_cmd, log, cwd=dest)

    venv_python = dest / ".venv" / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )

    # uv venvs do not include pip, so use `uv pip install` to force-reinstall
    # torch with the CUDA wheel when the project's own uv sync pulled a CPU build.
    if gpu_mode == "cuda" and spec.needs_gpu:
        index_url = _torch_index_url("cuda")
        assert index_url is not None
        log(f"Reinstalling torch/torchvision/torchaudio with CUDA wheels ({index_url})…")
        log("(this downloads ~3–5 GB; progress appears below)")
        # --no-deps: only swap the three CUDA binary wheels; do NOT let uv
        # re-resolve and upgrade other deps (e.g. numpy). uv sync already
        # locked everything to the versions in the tool's uv.lock — upgrading
        # numpy here breaks packages like matplotlib that were compiled against
        # NumPy 1.x.
        _run(
            ["uv", "pip", "install",
             "--python", str(venv_python),
             "torch", "torchvision", "torchaudio",
             "--index-url", index_url,
             "--force-reinstall",
             "--no-deps"],
            log, cwd=dest, env=tool_env(),
        )

    # Note: torchaudio 2.8+ uses torchcodec for save() but torchcodec ships
    # Linux-only wheels. On Windows we patch torchaudio.save() in tts.py with
    # a stdlib wave fallback instead — no extra package needed.

    if spec.needs_gpu and not _has_gpu():
        log("[warn] no NVIDIA GPU detected; this tool will run on CPU, which is much slower.")

    if not skip_model:
        _download_model(spec, dest, log)
    else:
        log("Skipping model download (--skip-model).")

    if spec.key == "index-tts":
        cfg = dest / "checkpoints" / "config.yaml"
        log(f"checkpoints/config.yaml present: {cfg.exists()}")
        _verify_tool_python(dest, "indextts.infer_v2", log)


def _write_managed_pyproject(spec: ToolSpec, dest: Path, gpu_mode: str) -> None:
    deps = ",\n    ".join(f'"{d}"' for d in spec.env_deps)
    dep_names = {re.split(r"[<>=!~\[ ]", d, maxsplit=1)[0] for d in spec.env_deps}
    torch_pkgs = [p for p in ("torch", "torchvision") if p in dep_names]
    index_url = _torch_index_url(gpu_mode) if torch_pkgs else None
    torch_index = "" if index_url is None else (
        "\n[[tool.uv.index]]\n"
        'name = "pytorch"\n'
        f'url = "{index_url}"\n'
        "explicit = true\n\n"
        "[tool.uv.sources]\n"
        + "".join(f'{p} = [{{ index = "pytorch" }}]\n' for p in torch_pkgs)
    )
    content = (
        "# Auto-generated by `mangaeasy install-tool`. Isolated env for "
        f"{spec.title}.\n"
        "[project]\n"
        f'name = "{spec.key}-env"\n'
        'version = "0.0.0"\n'
        'requires-python = ">=3.10"\n'
        "dependencies = [\n    "
        f"{deps}\n]\n"
        f"{torch_index}"
    )
    (dest / "pyproject.toml").write_text(content, encoding="utf-8")


def _install_managed_env(
    spec: ToolSpec,
    dest: Path,
    gpu_mode: str,
    clone: bool,
    ref: str | None,
    skip_model: bool,
    log: LogFn,
) -> None:
    _require(["uv"], log)
    dest.mkdir(parents=True, exist_ok=True)

    log("Writing isolated uv environment definition...")
    _write_managed_pyproject(spec, dest, gpu_mode)

    if spec.adapter:
        src = ASSETS_TOOLS / spec.adapter
        if not src.exists():
            raise InstallError(f"shipped adapter missing: {src}")
        shutil.copyfile(src, dest / spec.adapter)
        log(f"Installed adapter: {spec.adapter}")

    if clone and spec.git_url:
        upstream = dest / "upstream"
        log(f"Also cloning upstream repo into {upstream} (--clone)...")
        _require(["git"], log)
        _clone_or_update(spec.git_url, upstream, ref, log)

    if spec.needs_gpu and gpu_mode == "cpu":
        log("[note] CPU build — inference works everywhere but is slower than with an NVIDIA GPU.")

    _run(["uv", "sync"], log, cwd=dest)
    if spec.verify_import:
        _verify_tool_python(dest, spec.verify_import, log)
    if spec.model_repo:
        if skip_model:
            log("Skipping model download (--skip-model). Model weights will download from Hugging Face on first run.")
        else:
            _download_model(spec, dest, log)
    else:
        log("Model weights/code download from Hugging Face on first run.")


def install_tool(
    key: str,
    *,
    ref: str | None = None,
    dest: str | Path | None = None,
    skip_model: bool = False,
    gpu: str = "auto",          # auto | cuda | cpu
    clone: bool = False,
    update: bool = False,
    log: LogFn = print,
) -> Path:
    """Provision one external tool, or update an existing install.

    There's no separate code path for "update" — `_clone_or_update()` already
    pulls instead of cloning when the target has a `.git` dir, and `uv sync`/
    `hf download --local-dir` are both idempotent. `update=True` only changes
    the log line so the intent is visible; the GUI's "Update" button and
    `install-tool --update` both just call this the same way as a fresh
    install. Reused by the CLI and the app.
    """
    if key not in TOOLS:
        raise InstallError(f"unknown tool '{key}'. Known: {', '.join(TOOLS)}")
    spec = TOOLS[key]
    target = Path(dest).expanduser().resolve() if dest else (tools_home() / spec.key)
    target.parent.mkdir(parents=True, exist_ok=True)

    gpu_mode = gpu if gpu in ("cuda", "cpu") else default_gpu_mode()
    verb = "Updating" if update else "Installing"
    log(f"=== {verb} {spec.title} -> {target} ===")
    if gpu_mode == "cuda":
        detail = "NVIDIA GPU detected" if gpu == "auto" else "forced with --cuda"
        log(f"Torch build: CUDA ({detail})")
        if not _has_gpu():
            log("[warn] --cuda was forced but no NVIDIA GPU was detected (nvidia-smi missing).")
    else:
        detail = "no NVIDIA GPU / unsupported platform" if gpu == "auto" else "forced with --cpu"
        log(f"Torch build: CPU ({detail}) — works on any machine.")

    if spec.kind == "uv_project":
        _install_uv_project(spec, target, ref or spec.ref, skip_model, log, gpu_mode)
    else:
        _install_managed_env(spec, target, gpu_mode, clone, ref or spec.ref, skip_model, log)

    log(f"=== Done. mangaeasy resolves '{spec.key}' at: {target} ===")
    return target


# ── doctor ─────────────────────────────────────────────────────────────────────


def _update_available(path: Path, git_url: str | None) -> bool | None:
    """Cheap "is a newer commit available" check for a git-cloned tool —
    `git ls-remote` doesn't fetch any objects, just lists refs, so this stays
    fast even over a network. Returns None when it doesn't apply (no git
    clone here, e.g. a managed_env tool installed without --clone)."""
    if git_url is None or not (path / ".git").exists():
        return None
    try:
        local = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, **popen_kwargs(),
        )
        remote = subprocess.run(
            ["git", "ls-remote", git_url, "HEAD"],
            capture_output=True, text=True, timeout=10, **popen_kwargs(),
        )
        if local.returncode != 0 or remote.returncode != 0:
            return None
        remote_head = remote.stdout.split()[0] if remote.stdout.strip() else None
        return remote_head is not None and remote_head != local.stdout.strip()
    except Exception:
        return None


def doctor(*, check_updates: bool = False) -> dict:
    """Structured environment report (also consumed by the app).

    `check_updates=True` adds a per-tool `update_available` field via
    `git ls-remote` — opt-in and skipped by default since it needs network
    round-trips per installed tool; the default fast path stays local-only.
    """
    executables = {}
    for exe in ("git", "uv", "uvx", "ffmpeg", "ffprobe", "nvidia-smi"):
        # Use extended finder for nvidia-smi so Windows users without nvidia-smi
        # on PATH still see the real path instead of a false "missing".
        executables[exe] = _find_nvidia_smi() if exe == "nvidia-smi" else _which(exe)

    gpu_info = detect_gpu()

    # Machine-level GPU capability — what install-tool's build choice and the
    # pipeline's engine selection actually key on (nvidia-smi / platform), NOT
    # the main env's torch: heavy torch installs live in the isolated tool
    # envs, so the main env usually has no torch at all. Probing only torch
    # here used to report gpu_backend "cpu" on CUDA machines, which misled
    # agents and showed "CPU only" in the app's Setup tab.
    tools = {}
    for key, spec in TOOLS.items():
        path = resolve_tool_dir(key, required=False)
        tools[key] = {
            "title": spec.title,
            "installed": path is not None,
            "path": str(path) if path else None,
            "configured": bool(spec.git_url) or spec.kind == "managed_env",
            "git_url": spec.git_url,
            "needs_gpu": spec.needs_gpu,
            "notes": spec.notes,
            "update_available": _update_available(path, spec.git_url) if check_updates and path else None,
        }

    # faster-whisper lives in its own managed isolated env (not the main venv)
    # so it never conflicts with mangaeasy's deps or gets wiped by uv sync.
    whisper_installed = resolve_tool_dir("faster-whisper", required=False) is not None

    return {
        "tools_home": str(tools_home()),
        "git_lfs": _git_lfs_ok(),
        "gpu": gpu_info.has_nvidia,
        "cuda": gpu_info.cuda,
        "cuda_device": gpu_info.cuda_device,
        "mps": gpu_info.mps,
        "gpu_backend": gpu_info.backend,
        "whisper": whisper_installed,
        "executables": executables,
        "tools": tools,
    }


def doctor_main() -> int:
    check_updates = "--check-updates" in sys.argv[1:]
    if "--json" in sys.argv[1:]:
        print(json.dumps(doctor(check_updates=check_updates)))
        return 0

    report = doctor(check_updates=check_updates)
    print("mangaeasy doctor\n")
    print(f"Tools dir: {report['tools_home']}\n")

    print("Prerequisites:")
    for exe, where in report["executables"].items():
        mark = "ok " if where else "MISSING"
        print(f"  [{mark}] {exe:10s} {where or ''}")
    print(f"  [{'ok ' if report['git_lfs'] else 'MISSING'}] git-lfs")
    print(f"  [{'ok ' if report['gpu'] else '-- '}] NVIDIA GPU (nvidia-smi)")
    if not report["gpu"]:
        print("        No NVIDIA GPU found — that's fine: installs and the pipeline")
        print("        automatically use CPU builds (TTS/detection are just slower).")
    print()

    print("External AI tools:")
    for key, info in report["tools"].items():
        if info["installed"]:
            status = f"installed  {info['path']}"
        elif not info["configured"]:
            status = "not configured (set git_url in the manifest)"
        else:
            status = f"not installed  ->  mangaeasy install-tool {key}"
        print(f"  {key:12s} {status}")
    print()
    print("Install a tool with:  mangaeasy install-tool <name>")
    return 0


# ── CLI entry point ──────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="mangaeasy install-tool",
        description="Clone and set up an external AI tool in an isolated uv environment.",
    )
    parser.add_argument("name", nargs="?", help="Tool to install: " + ", ".join(TOOLS))
    parser.add_argument("--list", action="store_true", help="List available tools and exit.")
    parser.add_argument("--ref", help="Git branch/tag/commit to check out.")
    parser.add_argument("--dir", help="Install into this directory instead of the managed tools dir.")
    parser.add_argument("--skip-model", action="store_true", help="Skip downloading model weights.")
    gpu_group = parser.add_mutually_exclusive_group()
    gpu_group.add_argument("--cpu", action="store_true",
                           help="Force CPU torch builds (default: auto-detect).")
    gpu_group.add_argument("--cuda", action="store_true",
                           help="Force CUDA torch builds (default: auto-detect).")
    parser.add_argument("--clone", action="store_true", help="(managed envs) also clone the upstream repo for reference.")
    parser.add_argument("--update", action="store_true",
                         help="Already installed: pull the latest git ref / re-sync deps / "
                              "re-check model weights instead of a fresh install. (Re-running "
                              "install-tool on an existing install already does this -- --update "
                              "just makes the intent explicit in the log output.)")
    args = parser.parse_args()

    if args.list or not args.name:
        print(f"Available tools (installed into {tools_home()}):\n")
        for key, spec in TOOLS.items():
            ready = "ready" if (spec.git_url or spec.kind == "managed_env") else "needs git_url"
            print(f"  {key:12s} [{ready}]  {spec.title}")
            print(f"               {spec.notes}")
        print("\nUsage: mangaeasy install-tool <name> [--ref REF] [--cpu|--cuda] [--skip-model]")
        print(f"GPU auto-detect for this machine: {default_gpu_mode()} torch builds")
        return 0

    try:
        install_tool(
            args.name,
            ref=args.ref,
            dest=args.dir,
            skip_model=args.skip_model,
            gpu="cpu" if args.cpu else "cuda" if args.cuda else "auto",
            clone=args.clone,
            update=args.update,
        )
    except InstallError as exc:
        print(f"\n[install-tool] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
