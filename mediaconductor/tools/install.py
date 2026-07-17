"""mediaconductor.tools.install — provision the external AI tool environments.

These heavy tools (IndexTTS, MAGI v3, DeepSeek-OCR 2, Kokoro, Z-Image Turbo)
are deliberately kept in their own isolated ``uv`` environments instead of
being dependencies of mediaconductor, so their conflicting torch/transformers stacks
never clash with the core install. This module clones / sets them up into the
managed tools dir
(``<app_root>/.mangaeasy/tools`` by default — self-contained, removed along
with the install/repo folder).

Used by the ``mediaconductor install-tool`` and ``mediaconductor doctor``
subcommands. :func:`install_tool` also accepts a streaming log callback for
agent and service integrations.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from mediaconductor.brand import CLI_NAME, PRODUCT_NAME
from mediaconductor import runtime
from mediaconductor.tools.external import (
    python_command,
    resolve_tool_dir,
    tool_env,
    tools_home,
)
from mediaconductor.tools.hardware import (
    default_torch_build,
    detect_gpu,
    find_nvidia_smi,
    has_nvidia_gpu,
    nvidia_gpu_name,
    which,
)

LogFn = Callable[[str], None]
HF_CLI_REQUIREMENT = "huggingface-hub==1.23.0"
ASSETS_TOOLS = Path(__file__).resolve().parents[1] / "assets" / "tools"


class InstallError(RuntimeError):
    """Raised when a provisioning step fails; carries a human-readable message."""


# ── Manifest ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HfModelSpec:
    """One immutable Hugging Face snapshot installed beside a tool env."""

    repo: str
    revision: str
    subdir: str
    required_files: tuple[str, ...] = ()
    include: tuple[str, ...] = ()


@dataclass
class ToolSpec:
    key: str
    title: str
    kind: str  # "uv_project" (clone + uv sync) | "managed_env" (we author the env)
    git_url: str | None
    ref: str | None = None
    model_repo: str | None = None
    model_revision: str | None = None
    model_subdir: str | None = None
    required_model_files: tuple[str, ...] = ()
    extra_models: tuple[HfModelSpec, ...] = ()
    adapter: str | None = None          # asset filename to copy into the tool dir
    extra_adapters: list[str] = field(default_factory=list)  # more asset files to copy in
    env_deps: list[str] = field(default_factory=list)  # for managed_env
    exclude_extras: list[str] = field(default_factory=list)  # extras uv sync must skip
    verify_import: str | None = None    # module to import-check inside the env
    python: str = "3.12"
    sync_args: list[str] = field(default_factory=lambda: ["--all-extras"])
    preserve_upstream_torch: bool = False
    needs_gpu: bool = False
    notes: str = ""


TOOLS: dict[str, ToolSpec] = {
    "ace-step": ToolSpec(
        key="ace-step",
        title="ACE-Step 1.5 (song generation)",
        kind="uv_project",
        git_url="https://github.com/ace-step/ACE-Step-1.5",
        ref="dce621408bee8c31b4fcf4811682eb9359e1bc94",
        model_repo="ACE-Step/Ace-Step1.5",
        model_revision="19671f406d603126926c1b7e2adc169acbcade22",
        model_subdir="checkpoints",
        required_model_files=(
            "config.json",
            "acestep-v15-turbo/model.safetensors",
            "acestep-5Hz-lm-1.7B/model.safetensors",
            "Qwen3-Embedding-0.6B/model.safetensors",
            "vae/diffusion_pytorch_model.safetensors",
        ),
        adapter="generate_ace_step.py",
        verify_import="acestep",
        python="3.12",
        sync_args=["--frozen"],
        preserve_upstream_torch=True,
        needs_gpu=True,
        notes="ACE-Step 1.5 song generation. Pinned source + Hugging Face model revision; its upstream uv lock owns the platform-specific Torch stack.",
    ),
    "demucs": ToolSpec(
        key="demucs",
        title="Demucs 4.1 (vocal separation)",
        kind="managed_env",
        git_url="https://github.com/adefossez/demucs",
        ref="eeac1d15891af95b1288d2884b95baa3e5baa96c",
        model_repo="adefossez/HTDemucs-ft",
        model_revision="478be8a68f85418addd6f7baefd4be76522a4034",
        model_subdir="models/htdemucs-ft",
        required_model_files=(
            "htdemucs_ft.yaml",
            "04573f0d.safetensors",
            "92cfc3b6.safetensors",
            "d12395a8.safetensors",
            "f7e0c4bc.safetensors",
        ),
        adapter="separate_demucs.py",
        env_deps=[
            "demucs @ git+https://github.com/adefossez/demucs@eeac1d15891af95b1288d2884b95baa3e5baa96c",
            # Keep Torch explicit so the managed-env writer can route it to
            # the requested CUDA/CPU index instead of accepting a transitive,
            # platform-ambiguous wheel from Demucs.
            "torch>=2.1,<3",
            "huggingface-hub>=0.34,<2",
        ],
        verify_import="demucs",
        needs_gpu=True,
        notes="Maintained Demucs fork with a pinned local HTDemucs-ft snapshot and an offline-only adapter; the original facebookresearch repo is archived.",
    ),
    "whisperx": ToolSpec(
        key="whisperx",
        title="WhisperX 3.8.6 (lyrics timing)",
        kind="managed_env",
        git_url="https://github.com/m-bain/whisperX",
        ref="3ccc17b8de34f305300f8a3fd3c9f76ba820c0d0",
        model_repo="Systran/faster-whisper-large-v3",
        model_revision="edaa852ec7e145841d8ffdb056a99866b5f0a478",
        model_subdir="models/faster-whisper-large-v3",
        required_model_files=("config.json", "model.bin", "tokenizer.json"),
        extra_models=(HfModelSpec(
            repo="facebook/wav2vec2-base-960h",
            revision="22aad52d435eb6dbaf354bdad9b0da84ce7d6156",
            subdir="models/wav2vec2-base-960h",
            required_files=(
                "config.json", "model.safetensors", "preprocessor_config.json",
                "tokenizer_config.json", "vocab.json",
            ),
            include=(
                "config.json", "model.safetensors", "preprocessor_config.json",
                "special_tokens_map.json", "tokenizer_config.json", "vocab.json",
            ),
        ),),
        adapter="transcribe_whisperx.py",
        env_deps=[
            "whisperx==3.8.6",
            # Provides a wheel-bundled JIT model so WhisperX's Silero VAD never
            # needs to clone or execute code from Torch Hub at render time.
            "silero-vad==6.2.1",
            "torch~=2.8.0",
            "torchvision~=0.23.0",
            "torchaudio~=2.8.0",
        ],
        verify_import="whisperx",
        needs_gpu=True,
        notes="Word-level vocal transcription/timing. Supplied lyrics remain canonical; WhisperX provides timing evidence only.",
    ),
    "index-tts": ToolSpec(
        key="index-tts",
        title="IndexTTS 2",
        kind="uv_project",
        git_url="https://github.com/index-tts/index-tts",
        ref="13495845e3028f0bb6ca1462ad22aa0e76349e40",
        model_repo="IndexTeam/IndexTTS-2",
        model_revision="740dcaff396282ffb241903d150ac011cd4b1ede",
        model_subdir="checkpoints",
        required_model_files=(
            "config.yaml", "bpe.model", "gpt.pth", "s2mel.pth",
            "qwen0.6bemo4-merge/model.safetensors",
        ),
        # DeepSpeed is a training accelerator, unused for inference, and its
        # native build fails on most machines (needs the system CUDA toolkit
        # to exactly match torch's, plus aio/cufile libs Windows lacks).
        # accel (flash-attn) has no prebuilt wheel here and needs torch at
        # build time, so `uv sync` dies before the env exists; infer_v2 only
        # imports flash_attn when use_accel=True, which the adapter never
        # sets. webui is gradio-only and unused by the CLI pipeline.
        exclude_extras=["deepspeed", "accel", "webui"],
        # Upstream pins requires-python ">=3.10,<3.12" at this ref; the
        # ToolSpec default of 3.12 makes `uv sync` refuse the interpreter.
        python="3.11",
        needs_gpu=True,
        notes=f"High-quality voice-cloning TTS; the default engine for `{CLI_NAME} video` on NVIDIA GPU machines. ~5.9 GB model download from Hugging Face (config, gpt.pth, s2mel.pth, bpe.model).",
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
        ref="2a45bf09b43adc80778270a366372aaa148e2291",
        adapter="detect_magi.py",
        extra_adapters=["batch_detect_magi.py"],
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
        ref="2f3699ebbb96fa8af32212e8c170f2cc28730fad",
        model_repo="deepseek-ai/DeepSeek-OCR-2",
        model_revision="aaa02f3811945a91062062994c5c4a3f4c0af2b0",
        model_subdir="model",
        required_model_files=(
            "config.json", "processor_config.json", "tokenizer.json",
            "model.safetensors.index.json", "model-00001-of-000001.safetensors",
        ),
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
        ref="dfb907a02bba8152ca444717ca5d78747ccb4bec",
        env_deps=[
            "kokoro>=0.9",
            "torch>=2.5.0",
            "soundfile>=0.12",
            "numpy>=1.24.0",
        ],
        verify_import="kokoro",
        needs_gpu=False,
        notes=f"Light TTS (voice af_heart); the default engine for `{CLI_NAME} video` on machines without an NVIDIA GPU. Model downloads from Hugging Face on first run.",
    ),
    "gemma-4": ToolSpec(
        key="gemma-4",
        title="Gemma 4 E4B (local LLM, text + vision)",
        kind="managed_env",
        git_url=None,
        extra_models=(HfModelSpec(
            repo="ggml-org/gemma-4-E4B-it-GGUF",
            revision="06f24bb269339b2a19a5167199b81e89ef813c10",
            subdir="model",
            required_files=(
                "gemma-4-E4B-it-Q4_0.gguf",
                "mmproj-gemma-4-E4B-it-Q8_0.gguf",
            ),
            # Only the weights the runner loads — the repo also carries BF16 /
            # Q8_0 / mtp variants that would multiply the download for nothing.
            include=(
                "gemma-4-E4B-it-Q4_0.gguf",
                "mmproj-gemma-4-E4B-it-Q8_0.gguf",
            ),
        ),),
        adapter="run_gemma.py",
        # The runtime is a pinned llama.cpp release binary (installed by
        # _install_llama_runtime below), not a Python stack — this env only
        # needs Pillow so the adapter can downscale panel images before
        # base64-ing them into vision requests.
        env_deps=["pillow>=10.0.0"],
        verify_import="PIL",
        needs_gpu=False,
        notes="Google's Gemma 4 E4B instruct model (Apache-2.0) served by a pinned llama.cpp "
              "runtime — the local LLM behind `llm`, `crop-qa`, `characters --auto-draft`, and "
              "`narrate-auto`. ~5.4 GB model + ~0.6 GB vision projector from Hugging Face; runs "
              "on CPU (slow but fine) and offloads to GPU via Vulkan when available.",
    ),
    "z-image-turbo": ToolSpec(
        key="z-image-turbo",
        title="Z-Image Turbo (image generation)",
        kind="managed_env",
        git_url=None,
        model_repo="Tongyi-MAI/Z-Image-Turbo",
        model_revision="f332072aa78be7aecdf3ee76d5c247082da564a6",
        model_subdir="model",
        required_model_files=(
            "model_index.json",
            "text_encoder/model.safetensors.index.json",
            "text_encoder/model-00001-of-00003.safetensors",
            "text_encoder/model-00002-of-00003.safetensors",
            "text_encoder/model-00003-of-00003.safetensors",
            "transformer/diffusion_pytorch_model.safetensors.index.json",
            "transformer/diffusion_pytorch_model-00001-of-00003.safetensors",
            "transformer/diffusion_pytorch_model-00002-of-00003.safetensors",
            "transformer/diffusion_pytorch_model-00003-of-00003.safetensors",
            "vae/diffusion_pytorch_model.safetensors",
        ),
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
        proc = runtime.popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise InstallError(f"command not found: {cmd[0]} ({exc})") from exc
    assert proc.stdout is not None
    for line in proc.stdout:
        log(line.rstrip("\n"))
    code = proc.wait()
    if code != 0:
        raise InstallError(f"command failed (exit {code}): {' '.join(cmd)}")


def _pty_opt_in() -> bool:
    """True when the user explicitly asked for the winpty install PTY.

    The PTY used to be the Windows default for nicer line-flushed progress
    output, but winpty's agent always allocates a brand-new console, and on
    Windows 11 (default terminal = Windows Terminal) that console ignores the
    hidden-window request and appears as a visible blank terminal for the
    whole duration of every install step — the "blank terminal keeps popping
    up" bug, reproduced even after all subprocess spawns went through
    runtime.run/popen (winpty spawning is not `subprocess`, so the 2.1.0 fix
    never covered it). Pipe mode logs the same lines with no window, so it is
    now the default everywhere; set MEDIACONDUCTOR_INSTALL_PTY=1 to opt back
    into the PTY in a terminal where the popups don't bother you.
    """
    import os

    return os.environ.get("MEDIACONDUCTOR_INSTALL_PTY", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _run(cmd: list[str], log: LogFn, cwd: Path | None = None, env: dict | None = None) -> None:
    # Every install-tool subprocess (git clone, uv sync, hf download, …) runs
    # under tool_env() by default so its caches (UV_CACHE_DIR, HF_HOME, …)
    # always land under this install's own .mangaeasy/ dir, never the
    # system-wide default — explicit `env=` callers (e.g. one-off env tweaks
    # that already merged tool_env() themselves) are left untouched.
    if env is None:
        env = tool_env()
    log(f"$ {' '.join(str(c) for c in cmd)}")
    if sys.platform == "win32" and _pty_opt_in():
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
        return runtime.run(["git", "lfs", "version"], capture_output=True).returncode == 0
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
    # Tool repositories provide code only; model weights are downloaded from
    # Hugging Face by ``_download_model``. Keep Git transfers shallow and
    # blob-filtered so setup never pulls years of repository history (or LFS
    # payloads) just to materialize one pinned source revision.
    git_env = (
        {**tool_env(), "GIT_LFS_SKIP_SMUDGE": "1"}
        if skip_lfs_smudge else None
    )
    if (dest / ".git").exists():
        log(f"Updating existing clone at {dest}")
        if ref:
            # Fetch only the immutable requested object. GitHub serves commits
            # reachable from repository refs, including our pinned revisions.
            _run([
                "git", "-C", str(dest), "fetch", "--filter=blob:none",
                "--depth", "1", "--no-tags", "origin", ref,
            ], log, env=git_env)
            _run([
                "git", "-C", str(dest), "checkout", "--detach", "FETCH_HEAD",
            ], log, env=git_env)
        else:
            _run([
                "git", "-C", str(dest), "fetch", "--filter=blob:none",
                "--depth", "1", "--no-tags", "origin",
            ], log, env=git_env)
            _run([
                "git", "-C", str(dest), "merge", "--ff-only", "FETCH_HEAD",
            ], log, env=git_env)
    else:
        clone_command = [
            "git", "clone", "--filter=blob:none", "--depth", "1", "--no-tags",
        ]
        if ref:
            clone_command.append("--no-checkout")
        clone_command += [git_url, str(dest)]
        _run(clone_command, log, env=git_env)
        if ref:
            _run([
                "git", "-C", str(dest), "fetch", "--filter=blob:none",
                "--depth", "1", "--no-tags", "origin", ref,
            ], log, env=git_env)
            _run([
                "git", "-C", str(dest), "checkout", "--detach", "FETCH_HEAD",
            ], log, env=git_env)


def _is_model_payload(path: Path, root: Path) -> bool:
    """True for a non-empty, non-metadata model file below ``root``."""
    try:
        relative = path.relative_to(root)
        return (
            path.is_file()
            and path.stat().st_size > 0
            and not any(part.startswith(".") for part in relative.parts)
        )
    except (OSError, ValueError):
        return False


def _model_snapshot_present(root: Path, required_files: tuple[str, ...]) -> bool:
    """Validate one local snapshot without contacting Hugging Face."""
    if not root.is_dir():
        return False
    if required_files:
        return all(_is_model_payload(root / filename, root) for filename in required_files)
    return any(_is_model_payload(candidate, root) for candidate in root.rglob("*"))


def _download_hf_snapshot(
    repo: str,
    revision: str | None,
    target: Path,
    required_files: tuple[str, ...],
    include: tuple[str, ...],
    log: LogFn,
) -> None:
    log(f"Downloading model {repo} -> {target}")
    _require(["uvx"], log)
    # PYTHONUTF8=1 prevents Windows charmap errors when hf CLI prints Unicode
    # success symbols (e.g. ✓ U+2713) to a pipe that uses a legacy code page.
    env = {**tool_env(), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    # Plain huggingface-hub: since 1.x the `hf` CLI and Xet transfer are part
    # of the base package — the old `[cli,hf_xet]` extras no longer exist and
    # only produced install warnings.
    command = [
        "uvx", "--from", HF_CLI_REQUIREMENT,
        "hf", "download", repo, "--local-dir", str(target),
    ]
    if revision:
        command += ["--revision", revision]
    for pattern in include:
        command += ["--include", pattern]
    _run(
        command,
        log,
        env=env,
    )
    missing = [
        name for name in required_files
        if not _is_model_payload(target / name, target)
    ]
    if missing:
        raise InstallError(
            f"model snapshot {repo} is incomplete; missing or empty: {', '.join(missing)}"
        )
    if not required_files and not _model_snapshot_present(target, ()):
        raise InstallError(
            f"model snapshot {repo} contains no payload files under {target}"
        )


def _download_model(spec: ToolSpec, dest: Path, log: LogFn) -> None:
    if spec.model_repo:
        _download_hf_snapshot(
            spec.model_repo,
            spec.model_revision,
            dest / (spec.model_subdir or "checkpoints"),
            spec.required_model_files,
            (),
            log,
        )
    for model in spec.extra_models:
        _download_hf_snapshot(
            model.repo,
            model.revision,
            dest / model.subdir,
            model.required_files,
            model.include,
            log,
        )


def _required_model_files_present(spec: ToolSpec, dest: Path) -> bool:
    snapshots: list[tuple[Path, tuple[str, ...]]] = []
    if spec.model_repo:
        snapshots.append((
            dest / (spec.model_subdir or "checkpoints"),
            spec.required_model_files,
        ))
    snapshots.extend(
        (dest / model.subdir, model.required_files)
        for model in spec.extra_models
    )
    if not snapshots:
        return False
    return all(_model_snapshot_present(root, files) for root, files in snapshots)


def _verify_tool_python(dest: Path, import_check: str, log: LogFn) -> None:
    cmd = [*python_command(dest), "-c", f"import {import_check}; print('ok: {import_check}')"]
    _run(cmd, log, cwd=dest, env=tool_env())


def _install_adapter_files(spec: ToolSpec, dest: Path, log: LogFn) -> None:
    for adapter_name in ([spec.adapter] if spec.adapter else []) + spec.extra_adapters:
        src = ASSETS_TOOLS / adapter_name
        if not src.exists():
            raise InstallError(f"shipped adapter missing: {src}")
        shutil.copyfile(src, dest / adapter_name)
        log(f"Installed adapter: {adapter_name}")


def _install_uv_project(
    spec: ToolSpec, dest: Path, ref: str | None, skip_model: bool, log: LogFn,
    gpu_mode: str = "cpu",
) -> None:
    if not spec.git_url:
        raise InstallError(
            f"No git URL is configured for '{spec.key}'. Edit TOOLS['{spec.key}'].git_url "
            f"in mediaconductor/tools/install.py (or install it manually)."
        )
    _require(["git", "uv"], log)
    # Skip LFS smudge during clone so GitHub LFS bandwidth is never consumed.
    # Any large model files are fetched from Hugging Face by _download_model().
    _clone_or_update(spec.git_url, dest, ref, log, skip_lfs_smudge=True)

    _install_adapter_files(spec, dest, log)
    sync_cmd = ["uv", "sync", *spec.sync_args, "--python", spec.python]
    for extra in spec.exclude_extras:
        log(f"[info] skipping optional extra '{extra}' (not needed for inference)")
        sync_cmd += ["--no-extra", extra]
    _run(sync_cmd, log, cwd=dest)

    venv_python = dest / ".venv" / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )

    # uv venvs do not include pip, so use `uv pip install` to force-reinstall
    # torch with the CUDA wheel when the project's own uv sync pulled a CPU build.
    if gpu_mode == "cuda" and spec.needs_gpu and not spec.preserve_upstream_torch:
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
    elif spec.verify_import:
        _verify_tool_python(dest, spec.verify_import, log)


def _write_managed_pyproject(spec: ToolSpec, dest: Path, gpu_mode: str) -> None:
    deps = ",\n    ".join(f'"{d}"' for d in spec.env_deps)
    dep_names = {re.split(r"[<>=!~\[ ]", d, maxsplit=1)[0] for d in spec.env_deps}
    torch_pkgs = [p for p in ("torch", "torchvision", "torchaudio") if p in dep_names]
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
        f"# Auto-generated by `{CLI_NAME} install-tool`. Isolated env for "
        f"{spec.title}.\n"
        "[project]\n"
        f'name = "{spec.key}-env"\n'
        'version = "0.0.0"\n'
        f'requires-python = ">={spec.python},<{int(spec.python.split(".")[0])}.{int(spec.python.split(".")[1]) + 1}"\n'
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

    _install_adapter_files(spec, dest, log)

    if clone and spec.git_url:
        upstream = dest / "upstream"
        log(f"Also cloning upstream repo into {upstream} (--clone)...")
        _require(["git"], log)
        _clone_or_update(spec.git_url, upstream, ref, log)

    if spec.needs_gpu and gpu_mode == "cpu":
        log("[note] CPU build — inference works everywhere but is slower than with an NVIDIA GPU.")

    _run(["uv", "sync", "--python", spec.python], log, cwd=dest)
    if spec.key == "whisperx":
        # WhisperX's aligner otherwise downloads Punkt sentence data during
        # the first render. Provision the tiny dataset now into our managed
        # NLTK cache so normal English alignment has no surprise network step.
        env = tool_env()
        Path(env["NLTK_DATA"]).mkdir(parents=True, exist_ok=True)
        _run([
            *python_command(dest), "-c",
            "import nltk, os; "
            "ok = nltk.download('punkt_tab', download_dir=os.environ['NLTK_DATA'], "
            "quiet=False, raise_on_error=True); assert ok",
        ], log, cwd=dest, env=env)
    if spec.verify_import:
        _verify_tool_python(dest, spec.verify_import, log)
    if spec.model_repo or spec.extra_models:
        if skip_model:
            if spec.key == "demucs":
                if _required_model_files_present(spec, dest):
                    log("Skipping model download; the existing complete pinned Demucs snapshot will be used.")
                else:
                    log(
                        "Skipping model download (--skip-model). Demucs remains unavailable "
                        "until its pinned local model is installed; runtime downloads are disabled."
                    )
            else:
                log("Skipping model download (--skip-model). Model weights will download from Hugging Face on first run.")
        else:
            _download_model(spec, dest, log)
    else:
        log("Model weights/code download from Hugging Face on first run.")


# ── llama.cpp runtime (Gemma 4) ───────────────────────────────────────────────

# Pinned llama.cpp release that serves the Gemma 4 GGUF (day-one Gemma 4
# support landed well before this tag). Vulkan builds cover NVIDIA/AMD/Intel
# GPUs without the ~640 MB CUDA+cudart download; CPU builds work everywhere.
LLAMA_CPP_RELEASE = "b10064"


def llama_release_asset(system: str, arch: str, gpu: bool) -> str | None:
    """The llama.cpp release asset for this platform, or None when unsupported."""
    tag = LLAMA_CPP_RELEASE
    if system == "windows" and arch == "x64":
        flavor = "vulkan" if gpu else "cpu"
        return f"llama-{tag}-bin-win-{flavor}-x64.zip"
    if system == "linux" and arch == "x64":
        return f"llama-{tag}-bin-ubuntu-{'vulkan-' if gpu else ''}x64.tar.gz"
    if system == "darwin":
        return f"llama-{tag}-bin-macos-{'arm64' if arch == 'arm64' else 'x64'}.tar.gz"
    return None


def _extract_archive_all(archive: Path, dest: Path, log: LogFn) -> None:
    """Extract a zip/tar.gz completely into ``dest`` with traversal guards."""
    import tarfile
    import zipfile

    dest.mkdir(parents=True, exist_ok=True)
    resolved_dest = dest.resolve()

    def target_for(name: str) -> Path | None:
        member = Path(name.replace("\\", "/"))
        if member.is_absolute() or ".." in member.parts:
            raise InstallError(f"archive member escapes the target dir: {name}")
        target = (resolved_dest / member).resolve()
        if resolved_dest not in target.parents and target != resolved_dest:
            raise InstallError(f"archive member escapes the target dir: {name}")
        return target

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                target = target_for(info.filename)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out)
    else:
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                target = target_for(member.name)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    continue  # skip links/devices — nothing in these archives needs them
                src = tf.extractfile(member)
                if src is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as out:
                    shutil.copyfileobj(src, out)
    log(f"Extracted {archive.name} -> {dest}")


def find_llama_server(tool_dir: Path) -> Path | None:
    """Locate the llama-server binary inside a gemma-4 tool dir (or via env)."""
    import os

    configured = os.environ.get("MEDIACONDUCTOR_LLAMA_SERVER")
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_file() else None
    exe = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    runtime_dir = tool_dir / "llama"
    if not runtime_dir.is_dir():
        return None
    for candidate in sorted(runtime_dir.rglob(exe)):
        return candidate
    return None


def _install_llama_runtime(dest: Path, gpu_mode: str, log: LogFn) -> None:
    """Download the pinned llama.cpp release binaries into ``<dest>/llama``."""
    import platform as platform_module

    from mediaconductor.tools.vendored import _download, _make_executable

    system = platform_module.system().lower()
    machine = platform_module.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    asset = llama_release_asset(system, arch, gpu_mode == "cuda")
    if asset is None:
        raise InstallError(
            f"no pinned llama.cpp build for {system}/{arch}. Install llama.cpp yourself "
            "and point MEDIACONDUCTOR_LLAMA_SERVER at its llama-server binary."
        )
    runtime_dir = dest / "llama"
    if find_llama_server(dest) is not None:
        log(f"llama.cpp runtime already present under {runtime_dir}")
        return
    url = (
        "https://github.com/ggml-org/llama.cpp/releases/download/"
        f"{LLAMA_CPP_RELEASE}/{asset}"
    )
    archive = _download(url, dest / "_dl" / asset, log)
    shutil.rmtree(runtime_dir, ignore_errors=True)
    _extract_archive_all(archive, runtime_dir, log)
    shutil.rmtree(dest / "_dl", ignore_errors=True)
    server = find_llama_server(dest)
    if server is None:
        raise InstallError(f"llama-server not found after extracting {asset}")
    if sys.platform != "win32":
        for binary in server.parent.iterdir():
            if binary.is_file():
                _make_executable(binary)
    log(f"llama.cpp runtime ready: {server}")


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
    the log line so the intent is visible; automated update callers and
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

    if spec.key == "gemma-4":
        _install_llama_runtime(target, gpu_mode, log)

    # ``None`` means this integration resolves its model at runtime and the
    # installer cannot truthfully attest to a local snapshot (Kokoro, MAGI,
    # optional Faster Whisper). A boolean is reserved for snapshots managed by
    # this installer.
    model_downloaded: bool | None = None
    if spec.model_repo or spec.extra_models:
        model_downloaded = not skip_model
        if skip_model and _required_model_files_present(spec, target):
            model_downloaded = True
    marker = {
        "schema_version": 1,
        "tool": spec.key,
        "source": spec.git_url,
        "source_revision": ref or spec.ref,
        "model": spec.model_repo,
        "model_revision": spec.model_revision,
        "additional_models": [
            {"repo": model.repo, "revision": model.revision, "subdir": model.subdir}
            for model in spec.extra_models
        ],
        "model_downloaded": model_downloaded,
        "python": spec.python,
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    (target / "READY.json").write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")

    log(f"=== Done. {PRODUCT_NAME} resolves '{spec.key}' at: {target} ===")
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
        local = runtime.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        remote = runtime.run(
            ["git", "ls-remote", git_url, "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if local.returncode != 0 or remote.returncode != 0:
            return None
        remote_head = remote.stdout.split()[0] if remote.stdout.strip() else None
        return remote_head is not None and remote_head != local.stdout.strip()
    except Exception:
        return None


def _tool_health(path: Path | None, spec: ToolSpec) -> tuple[bool, list[str]]:
    if path is None:
        return False, ["tool directory is missing"]
    reasons: list[str] = []
    marker_path = path / "READY.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if not isinstance(marker, dict):
            raise ValueError("READY.json must contain an object")
    except (OSError, ValueError):
        marker = None
        reasons.append("READY.json is missing or invalid; re-run install-tool")
    if marker is not None and marker.get("tool") != spec.key:
        reasons.append("READY.json belongs to a different tool")
    python_paths = [path / ".venv" / "Scripts" / "python.exe", path / ".venv" / "bin" / "python"]
    if not any(candidate.is_file() for candidate in python_paths):
        reasons.append("isolated Python interpreter is missing")
    for adapter in ([spec.adapter] if spec.adapter else []) + spec.extra_adapters:
        if not (path / adapter).is_file():
            reasons.append(f"adapter is missing: {adapter}")
    if (
        (spec.model_repo or spec.extra_models)
        and marker is not None
        and marker.get("model_downloaded") is not True
    ):
        reasons.append("model download was deferred")
    if spec.model_repo:
        model_root = path / (spec.model_subdir or "checkpoints")
        if not model_root.is_dir():
            reasons.append(
                f"model snapshot directory is missing: {spec.model_subdir or 'checkpoints'}"
            )
        elif spec.required_model_files:
            for filename in spec.required_model_files:
                if not _is_model_payload(model_root / filename, model_root):
                    reasons.append(f"model snapshot file is missing or empty: {filename}")
        elif not _model_snapshot_present(model_root, ()):
            reasons.append(f"model snapshot contains no payload files: {spec.model_repo}")
    for model in spec.extra_models:
        root = path / model.subdir
        if not root.is_dir():
            reasons.append(f"model snapshot directory is missing: {model.subdir}")
        elif model.required_files:
            for filename in model.required_files:
                if not _is_model_payload(root / filename, root):
                    reasons.append(
                        f"model snapshot file is missing or empty: {model.repo}/{filename}"
                    )
        elif not _model_snapshot_present(root, ()):
            reasons.append(f"model snapshot contains no payload files: {model.repo}")
    if spec.key == "gemma-4" and find_llama_server(path) is None:
        reasons.append("llama.cpp runtime is missing; re-run install-tool gemma-4")
    return not reasons, reasons


def doctor(*, check_updates: bool = False, mode: str | None = None) -> dict:
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
    selected_tools = set(TOOLS)
    if mode:
        from mediaconductor.tools.setup import MODE_TOOLS
        selected_tools = set(MODE_TOOLS[mode])
    for key, spec in TOOLS.items():
        if key not in selected_tools:
            continue
        path = resolve_tool_dir(key, required=False)
        healthy, health_problems = _tool_health(path, spec)
        tools[key] = {
            "title": spec.title,
            "installed": path is not None,
            "ready": healthy,
            "health_problems": health_problems,
            "path": str(path) if path else None,
            "configured": bool(spec.git_url) or spec.kind == "managed_env",
            "git_url": spec.git_url,
            "needs_gpu": spec.needs_gpu,
            "notes": spec.notes,
            "update_available": _update_available(path, spec.git_url) if check_updates and path else None,
        }

    # faster-whisper lives in its own managed isolated env (not the main venv)
    # so it never conflicts with mediaconductor's deps or gets wiped by uv sync.
    whisper_installed = resolve_tool_dir("faster-whisper", required=False) is not None

    return {
        "tools_home": str(tools_home()),
        "mode": mode,
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
    parser = argparse.ArgumentParser(prog=f"{CLI_NAME} doctor")
    parser.add_argument("--mode", choices=("manga-video", "ai-story", "song-video"))
    parser.add_argument("--check-updates", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    if args.as_json:
        print(json.dumps(doctor(check_updates=args.check_updates, mode=args.mode)))
        return 0

    report = doctor(check_updates=args.check_updates, mode=args.mode)
    print(f"{PRODUCT_NAME} doctor\n")
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
        if info["ready"]:
            status = f"installed  {info['path']}"
        elif info["installed"]:
            status = f"INCOMPLETE  {info['path']} ({'; '.join(info['health_problems'])})"
        elif not info["configured"]:
            status = "not configured (set git_url in the manifest)"
        else:
            status = f"not installed  ->  {CLI_NAME} install-tool {key}"
        print(f"  {key:12s} {status}")
    print()
    print(f"Install a tool with:  {CLI_NAME} install-tool <name>")
    return 0


# ── CLI entry point ──────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} install-tool",
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
        print(f"\nUsage: {CLI_NAME} install-tool <name> [--ref REF] [--cpu|--cuda] [--skip-model]")
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
