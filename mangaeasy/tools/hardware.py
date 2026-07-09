"""Hardware detection helpers shared by installers and runtime choices."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from mangaeasy.runtime import popen_kwargs


@dataclass(frozen=True)
class GpuInfo:
    has_nvidia: bool
    cuda: bool
    cuda_device: str | None
    mps: bool
    backend: str


def which(exe: str) -> str | None:
    return shutil.which(exe)


def find_nvidia_smi() -> str | None:
    """Return nvidia-smi from PATH or common Windows driver locations."""
    found = which("nvidia-smi")
    if found:
        return found
    if sys.platform == "win32":
        program_files = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles", r"C:\Program Files")
        for candidate in (
            Path(r"C:\Windows\System32\nvidia-smi.exe"),
            Path(program_files) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
        ):
            if candidate.is_file():
                return str(candidate)
    return None


def has_nvidia_gpu() -> bool:
    if find_nvidia_smi() is not None:
        return True
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["wmic", "path", "Win32_VideoController", "get", "AdapterCompatibility"],
                capture_output=True,
                text=True,
                timeout=8,
                **popen_kwargs(),
            ).stdout
            return "NVIDIA" in out
        except Exception:
            return False
    return False


def nvidia_gpu_name() -> str | None:
    smi = find_nvidia_smi()
    if not smi:
        return None
    try:
        out = subprocess.run(
            [smi, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=8,
            **popen_kwargs(),
        ).stdout.strip()
        return out.splitlines()[0].strip() if out else None
    except Exception:
        return None


def detect_gpu() -> GpuInfo:
    """Detect the best available ML backend without requiring torch."""
    cuda_available = False
    cuda_device: str | None = None
    mps_available = False

    try:
        import torch  # type: ignore[import-untyped]

        cuda_available = bool(torch.cuda.is_available())
        if cuda_available:
            cuda_device = torch.cuda.get_device_name(0)
        mps_backend = getattr(torch.backends, "mps", None)
        mps_available = bool(mps_backend is not None and mps_backend.is_available())
    except Exception:
        pass

    nvidia = has_nvidia_gpu()
    if not cuda_available and sys.platform in ("win32", "linux") and nvidia:
        cuda_available = True
        cuda_device = nvidia_gpu_name()
    if not mps_available and sys.platform == "darwin":
        mps_available = platform.machine() == "arm64"

    backend = "cuda" if cuda_available else "mps" if mps_available else "cpu"
    return GpuInfo(
        has_nvidia=nvidia,
        cuda=cuda_available,
        cuda_device=cuda_device,
        mps=mps_available,
        backend=backend,
    )


def default_torch_build() -> str:
    """Torch build family for isolated tool envs: cuda on NVIDIA, else cpu."""
    if sys.platform in ("win32", "linux") and has_nvidia_gpu():
        return "cuda"
    return "cpu"
