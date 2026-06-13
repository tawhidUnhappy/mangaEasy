"""mangaeasy.tools.install — provision the external AI tool environments.

These heavy tools (IndexTTS, MAGI v3, Kokoro) are deliberately kept in
their own isolated ``uv`` environments instead of being dependencies of
mangaeasy, so their conflicting torch/transformers stacks never clash with the
core install. This module clones / sets them up into the managed tools dir
(``~/.mangaeasy/tools`` by default) so a globally-installed ``mangaeasy`` can
find them from any folder.

Used by the ``mangaeasy install-tool`` and ``mangaeasy doctor`` subcommands, and
by the control-center app, which reuses :func:`install_tool` with a streaming
log callback.
"""

from __future__ import annotations

import argparse
import os
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
}


# ── Shell helpers ─────────────────────────────────────────────────────────────


def _run(cmd: list[str], log: LogFn, cwd: Path | None = None, env: dict | None = None) -> None:
    log(f"$ {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
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


def _which(exe: str) -> str | None:
    return shutil.which(exe)


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
    """Return the path to nvidia-smi, checking PATH and standard Windows install locations."""
    where = _which("nvidia-smi")
    if where:
        return where
    if sys.platform == "win32":
        # NVIDIA drivers install nvidia-smi to System32 or the NVSMI folder, but
        # neither location is always on PATH.
        prog = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles", r"C:\Program Files")
        for candidate in (
            Path(r"C:\Windows\System32\nvidia-smi.exe"),
            Path(prog) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
        ):
            if candidate.is_file():
                return str(candidate)
    return None


def _has_gpu() -> bool:
    if _find_nvidia_smi() is not None:
        return True
    if sys.platform == "win32":
        # Last resort: WMI query for NVIDIA-branded video controllers. Catches
        # setups where nvidia-smi is present but not readable by _find_nvidia_smi.
        try:
            out = subprocess.run(
                ["wmic", "path", "Win32_VideoController", "get", "AdapterCompatibility"],
                capture_output=True, text=True, timeout=8,
            ).stdout
            if "NVIDIA" in out:
                return True
        except Exception:
            pass
    return False


def default_gpu_mode() -> str:
    """Pick the torch build that actually fits this machine.

    CUDA wheels only exist for Windows/Linux and only help with an NVIDIA GPU;
    everyone else (macOS, AMD, Intel, plain CPU) gets standard PyPI builds.
    """
    if sys.platform in ("win32", "linux") and _has_gpu():
        return "cuda"
    return "cpu"


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
        clone_env = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"} if skip_lfs_smudge else None
        _run(["git", "clone", git_url, str(dest)], log, env=clone_env)
        if ref:
            _run(["git", "-C", str(dest), "checkout", ref], log)


def _download_model(spec: ToolSpec, dest: Path, log: LogFn) -> None:
    if not spec.model_repo:
        return
    target = dest / (spec.model_subdir or "checkpoints")
    log(f"Downloading model {spec.model_repo} -> {target}")
    _require(["uvx"], log)
    _run(
        [
            "uvx", "--from", "huggingface-hub[cli,hf_xet]",
            "hf", "download", spec.model_repo, "--local-dir", str(target),
        ],
        log,
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

    # uv venvs do not include pip, so use `uv pip install` to force-reinstall
    # torch with the CUDA wheel when the project's own uv sync pulled a CPU build.
    if gpu_mode == "cuda" and spec.needs_gpu:
        index_url = _torch_index_url("cuda")
        assert index_url is not None
        log(f"Reinstalling torch with CUDA wheels ({index_url})…")
        venv_python = dest / ".venv" / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )
        _run(
            ["uv", "pip", "install",
             "--python", str(venv_python),
             "torch", "torchvision",
             "--index-url", index_url,
             "--force-reinstall", "--quiet"],
            log, cwd=dest, env=tool_env(),
        )

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
    dep_names = {re.split(r"[<>=!~\[ ]", d, 1)[0] for d in spec.env_deps}
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
    spec: ToolSpec, dest: Path, gpu_mode: str, clone: bool, ref: str | None, log: LogFn
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
    log("Model weights/code download from Hugging Face on first run.")


def install_tool(
    key: str,
    *,
    ref: str | None = None,
    dest: str | Path | None = None,
    skip_model: bool = False,
    gpu: str = "auto",          # auto | cuda | cpu
    clone: bool = False,
    log: LogFn = print,
) -> Path:
    """Provision one external tool. Reused by the CLI and the app."""
    if key not in TOOLS:
        raise InstallError(f"unknown tool '{key}'. Known: {', '.join(TOOLS)}")
    spec = TOOLS[key]
    target = Path(dest).expanduser().resolve() if dest else (tools_home() / spec.key)
    target.parent.mkdir(parents=True, exist_ok=True)

    gpu_mode = gpu if gpu in ("cuda", "cpu") else default_gpu_mode()
    log(f"=== Installing {spec.title} -> {target} ===")
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
        _install_managed_env(spec, target, gpu_mode, clone, ref or spec.ref, log)

    log(f"=== Done. mangaeasy resolves '{spec.key}' at: {target} ===")
    return target


# ── doctor ─────────────────────────────────────────────────────────────────────


def doctor() -> dict:
    """Structured environment report (also consumed by the app)."""
    executables = {}
    for exe in ("git", "uv", "uvx", "ffmpeg", "ffprobe", "nvidia-smi"):
        # Use extended finder for nvidia-smi so Windows users without nvidia-smi
        # on PATH still see the real path instead of a false "missing".
        executables[exe] = _find_nvidia_smi() if exe == "nvidia-smi" else _which(exe)

    # Check if torch CUDA is actually usable in this Python environment.
    cuda_available = False
    cuda_device: str | None = None
    try:
        import torch  # type: ignore[import-untyped]
        cuda_available = bool(torch.cuda.is_available())
        if cuda_available:
            cuda_device = torch.cuda.get_device_name(0)
    except Exception:
        pass

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
        }

    return {
        "tools_home": str(tools_home()),
        "git_lfs": _git_lfs_ok(),
        "gpu": _has_gpu(),
        "cuda": cuda_available,
        "cuda_device": cuda_device,
        "executables": executables,
        "tools": tools,
    }


def doctor_main() -> int:
    report = doctor()
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
        )
    except InstallError as exc:
        print(f"\n[install-tool] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
