from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from mangaeasy.runtime import is_frozen


TOOL_ENVS = {
    "kokoro-82m": ("KOKORO_ROOT",),
    "index-tts": ("INDEX_TTS_ROOT", "INDEX_TTS_DIR"),
    "magi-v3": ("MAGI_V3_ROOT", "MAGI_V3_DIR"),
    "deepseek-ocr2": ("DEEPSEEK_OCR2_ROOT", "DEEPSEEK_OCR2_DIR"),
    "z-image-turbo": ("Z_IMAGE_TURBO_ROOT", "Z_IMAGE_TURBO_DIR"),
}

TOOL_ENV = {tool_name: env_vars[0] for tool_name, env_vars in TOOL_ENVS.items()}


def package_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_frozen_root() -> Path:
    """Writable data root for a frozen build running without MANGAEASY_ROOT.

    The Electron app always sets MANGAEASY_ROOT before spawning the backend,
    so this only matters when the frozen CLI is run standalone. The exe's own
    folder is not reliably writable everywhere: inside a macOS .app bundle it
    is sealed/read-only, and a Linux AppImage mount or /opt install is
    read-only too — those fall back to the platform's standard data dir
    (mirrors desktop/src/main/paths.ts's appRoot()).
    """
    exe_dir = Path(sys.executable).resolve().parent
    if sys.platform == "win32":
        return exe_dir
    if sys.platform == "darwin":
        if ".app/Contents" in exe_dir.as_posix():
            return Path.home() / "Library" / "Application Support" / "mangaEasy"
        return exe_dir
    # Linux: use the exe dir when it's writable (plain tar.gz extract),
    # otherwise XDG data home (AppImage mount, /opt install).
    if os.access(exe_dir, os.W_OK):
        return exe_dir
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "mangaEasy"


def app_root() -> Path:
    """Directory this install of mangaEasy keeps its data in.

    Frozen build: a per-platform writable root (see _default_frozen_root).
    Dev checkout: the repo root (parent of the ``mangaeasy`` package).
    Electron sets MANGAEASY_ROOT explicitly when it spawns the backend, so
    that always wins when present.
    """
    configured = os.environ.get("MANGAEASY_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    if is_frozen():
        return _default_frozen_root()
    return package_root()


def mangaeasy_home() -> Path:
    """This install's own data dir: AI tool envs, app state, shared caches.

    Lives at ``<app_root>/.mangaeasy`` so deleting the install/repo folder
    removes it too — nothing is written to the user's home directory.
    Override with MANGAEASY_HOME (e.g. to share tool installs across
    multiple dev checkouts).
    """
    configured = os.environ.get("MANGAEASY_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (app_root() / ".mangaeasy").resolve()


def tools_home() -> Path:
    """Managed dir where `mangaeasy install-tool` puts external tool envs.

    Default `<app_root>/.mangaeasy/tools` — self-contained, deleted along
    with the install/repo folder. Override with MANGAEASY_TOOLS_DIR.
    """
    configured = os.environ.get("MANGAEASY_TOOLS_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return mangaeasy_home() / "tools"


def candidate_roots() -> list[Path]:
    # Only the managed install dir — tools must be provisioned with
    # `mangaeasy install-tool` rather than relied on as sibling directories.
    return [tools_home()]


def env_vars_for(tool_name: str, env_var: str | None = None) -> tuple[str, ...]:
    aliases = TOOL_ENVS.get(tool_name, ())
    if env_var is None:
        return aliases
    return (env_var, *(alias for alias in aliases if alias != env_var))


def resolve_tool_dir(tool_name: str, env_var: str | None = None, required: bool = True) -> Path | None:
    checked_envs = env_vars_for(tool_name, env_var)
    for checked_env in checked_envs:
        configured = os.environ.get(checked_env)
        if configured:
            path = Path(configured).expanduser().resolve()
            if path.exists():
                return path
            if required:
                raise FileNotFoundError(f"{checked_env} points to a missing tool folder: {path}")

    for root in candidate_roots():
        path = (root / tool_name).resolve()
        if path.exists():
            return path

    if not required:
        return None

    roots = ", ".join(str(root / tool_name) for root in candidate_roots())
    env_hint = f" or set one of: {', '.join(checked_envs)}" if checked_envs else ""
    raise FileNotFoundError(
        f"Could not find external tool '{tool_name}'. Put it at one of: {roots}{env_hint}."
    )


def python_command(tool_dir: Path) -> list[str]:
    windows_python = tool_dir / ".venv" / "Scripts" / "python.exe"
    posix_python = tool_dir / ".venv" / "bin" / "python"
    if windows_python.exists():
        return [str(windows_python)]
    if posix_python.exists():
        return [str(posix_python)]
    return ["uv", "run", "--project", str(tool_dir), "python"]


def _share_caches() -> bool:
    """True when the user opts into inheriting ambient cache locations."""
    return os.environ.get("MANGAEASY_SHARE_CACHES", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def tool_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Env for subprocesses that run inside an isolated tool venv.

    Every cache an external tool (or `uv` itself) might write is pinned under
    this install's own `.mangaeasy/` dir, so the "everything mangaEasy writes
    lives in one folder" promise holds and deleting the install folder leaves
    nothing behind. These are **force-set** (they override an inherited
    ``HF_HOME`` / ``UV_CACHE_DIR`` / ... from the ambient environment): a
    global cache var the user exported for *other* tools would otherwise
    silently scatter multi-GB model downloads outside the install folder.
    Set ``MANGAEASY_SHARE_CACHES=1`` to deliberately defer to those inherited
    locations instead (a shared cross-project cache); then they are only
    filled in when absent.

    Always drops VIRTUAL_ENV/PYTHONHOME inherited from mangaeasy's own
    process. Every caller launches a subprocess by an explicit absolute
    python.exe path into some *other* tool's isolated venv (kokoro, IndexTTS,
    MAGI, ...) -- but a few of those tools (e.g. misaki/spacy's
    `en_core_web_sm` auto-download) fall back to bare `uv pip install` when
    no `pip` module is present, and uv resolves that against VIRTUAL_ENV if
    it's set. Left inherited, that silently installs into mangaeasy's own
    venv instead of the tool's, which then can't find what it just
    "successfully" installed.
    """
    env = dict(base or os.environ)
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONHOME", None)
    hf_cache = mangaeasy_home() / "hf_cache"
    # Force these under .mangaeasy so an inherited global HF_HOME/UV_CACHE_DIR
    # can't leak downloads out of the install folder (opt out with
    # MANGAEASY_SHARE_CACHES=1, which reverts them to setdefault semantics).
    set_cache = env.setdefault if _share_caches() else env.__setitem__
    set_cache("HF_HOME", str(hf_cache))
    set_cache("HF_HUB_CACHE", str(hf_cache / "hub"))
    set_cache("TRANSFORMERS_CACHE", str(hf_cache / "hub"))
    set_cache("TORCH_HOME", str(mangaeasy_home() / "torch_cache"))
    set_cache("UV_CACHE_DIR", str(mangaeasy_home() / "uv_cache"))
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    env.setdefault("HF_XET_HIGH_PERFORMANCE", "1")
    env.setdefault("TOKENIZERS_PARALLELISM", "false")

    espeak_root = Path("C:/Program Files/eSpeak NG")
    if espeak_root.exists():
        env["PATH"] = f"{espeak_root}{os.pathsep}{env.get('PATH', '')}"
        env.setdefault("ESPEAK_DATA_PATH", str(espeak_root / "espeak-ng-data"))
    return env


def resolve_device(requested: str) -> str:
    """Resolve a `--device {auto,cuda,mps,cpu}`-style flag against this machine.

    `auto` prefers CUDA (NVIDIA), then MPS (Apple Silicon), then CPU. AMD
    ROCm / non-NVIDIA Linux GPUs aren't probed for — they fall through to
    CPU, same as today.
    """
    if requested != "auto":
        return requested
    try:
        import torch  # type: ignore[import-untyped]
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if sys.platform == "darwin" and getattr(torch.backends, "mps", None) is not None \
            and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def run_tool_python(tool_name: str, script: Path, args: list[str], *, env_var: str | None = None) -> None:
    tool_dir = resolve_tool_dir(tool_name, env_var)
    assert tool_dir is not None
    command = [*python_command(tool_dir), str(script), *args]
    print(f"[tool:{tool_name}] {tool_dir}", flush=True)
    print(" ".join(command), flush=True)
    subprocess.run(command, cwd=tool_dir, env=tool_env(), check=True)


def _tools_report() -> dict[str, str | None]:
    return {
        tool_name: (str(path) if (path := resolve_tool_dir(tool_name, required=False)) else None)
        for tool_name in TOOL_ENVS
    }


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Show where external tool envs resolve.")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit the report as a single JSON object on stdout.")
    args = parser.parse_args()

    report = _tools_report()
    if args.as_json:
        print(json.dumps({"tools_home": str(tools_home()), "tools": report}, ensure_ascii=False))
        return 0

    print("External tool lookup:")
    for tool_name, env_vars in TOOL_ENVS.items():
        status = report[tool_name] or "not found"
        print(f"  {tool_name:10s} {status}  ({', '.join(env_vars)})")
    print()
    print(f"Tools are installed to: {tools_home()}")
    print("Run `mangaeasy install-tool <name>` to provision a tool.")
    print("Override with MANGAEASY_TOOLS_DIR or per-tool env vars above.")
    return 0


def where_main() -> int:
    """`mangaeasy where [--json]` — resolved paths + environment facts.

    The first command a script or AI agent should run: answers "where does
    this install keep everything on THIS machine" without guessing.
    """
    import argparse
    import json

    from mangaeasy import __version__
    from mangaeasy.tools.vendored import vendored_bin_dirs

    parser = argparse.ArgumentParser(description="Show this install's resolved paths.")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit the report as a single JSON object on stdout.")
    args = parser.parse_args()

    info = {
        "version": __version__,
        "platform": sys.platform,
        "frozen": is_frozen(),
        "executable": sys.executable,
        "app_root": str(app_root()),
        "mangaeasy_home": str(mangaeasy_home()),
        "tools_home": str(tools_home()),
        "vendored_bin_dirs": [str(d) for d in vendored_bin_dirs()],
        "env_overrides": {
            name: os.environ.get(name)
            for name in ("MANGAEASY_ROOT", "MANGAEASY_HOME", "MANGAEASY_TOOLS_DIR")
        },
    }
    if args.as_json:
        print(json.dumps(info, ensure_ascii=False))
        return 0
    for key, value in info.items():
        print(f"  {key:18s} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
