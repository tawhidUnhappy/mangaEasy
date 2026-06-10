from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


TOOL_ENVS = {
    "kokoro-82m": ("KOKORO_ROOT",),
    "index-tts": ("INDEX_TTS_ROOT", "INDEX_TTS_DIR"),
    "magi-v3": ("MAGI_V3_ROOT", "MAGI_V3_DIR"),
}

TOOL_ENV = {tool_name: env_vars[0] for tool_name, env_vars in TOOL_ENVS.items()}


def package_root() -> Path:
    return Path(__file__).resolve().parents[2]


def mangaeasy_home() -> Path:
    """Per-user mangaEasy data dir (default ~/.mangaeasy, override MANGAEASY_HOME)."""
    configured = os.environ.get("MANGAEASY_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".mangaeasy").resolve()


def tools_home() -> Path:
    """Managed dir where `mangaeasy install-tool` puts external tool envs.

    Default ~/.mangaeasy/tools so a globally-installed mangaeasy can find the
    tools from any working directory. Override with MANGAEASY_TOOLS_DIR.
    """
    configured = os.environ.get("MANGAEASY_TOOLS_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return mangaeasy_home() / "tools"


def candidate_roots() -> list[Path]:
    cwd = Path.cwd().resolve()
    root = package_root().resolve()
    # Managed tools dir first (install-once-use-anywhere), then folders relative
    # to where the user is working, then the package location.
    candidates = [tools_home(), cwd, cwd.parent, root, root.parent]
    seen: set[Path] = set()
    result: list[Path] = []
    for path in candidates:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


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


def tool_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base or os.environ)
    hf_cache = Path.cwd().resolve() / ".hf_cache"
    if "HF_HOME" not in env:
        env["HF_HOME"] = str(hf_cache)
    env.setdefault("HF_XET_HIGH_PERFORMANCE", "1")
    env.setdefault("TOKENIZERS_PARALLELISM", "false")

    espeak_root = Path("C:/Program Files/eSpeak NG")
    if espeak_root.exists():
        env["PATH"] = f"{espeak_root}{os.pathsep}{env.get('PATH', '')}"
        env.setdefault("ESPEAK_DATA_PATH", str(espeak_root / "espeak-ng-data"))
    return env


def run_tool_python(tool_name: str, script: Path, args: list[str], *, env_var: str | None = None) -> None:
    tool_dir = resolve_tool_dir(tool_name, env_var)
    assert tool_dir is not None
    command = [*python_command(tool_dir), str(script), *args]
    print(f"[tool:{tool_name}] {tool_dir}", flush=True)
    print(" ".join(command), flush=True)
    subprocess.run(command, cwd=tool_dir, env=tool_env(), check=True)


def main() -> int:
    print("External tool lookup:")
    for tool_name, env_vars in TOOL_ENVS.items():
        path = resolve_tool_dir(tool_name, required=False)
        status = str(path) if path else "not found"
        print(f"  {tool_name:10s} {status}  ({', '.join(env_vars)})")
    print()
    print("Install tools as sibling uv projects when you need them, for example:")
    print("  ./kokoro-82m")
    print("  ./index-tts")
    print("  ./magi-v3")
    print("Each tool keeps its own .venv and Python.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
