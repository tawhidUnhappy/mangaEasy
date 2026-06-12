"""mangaeasy.config
Central config loader and project-root-relative cache directory setup.

Two config files:
  config.json         — per-manga / per-run settings (name, chapter, bgm file …)
  config.system.json  — system-wide settings that rarely change (resolution,
                        fps, encoder params, whisper model, ports …)

Import this module BEFORE any ML library (torch, transformers) so that
HF_HOME and TORCH_HOME are set to project-local paths in time.
"""

import json
import os
import sys
from pathlib import Path

_PACKAGE_ROOT: Path = Path(__file__).resolve().parent.parent


def _project_root() -> Path:
    configured = os.environ.get("MANGAEASY_PROJECT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.cwd().resolve()


PROJECT_ROOT: Path = _project_root()

CONFIG_FILE:        Path = PROJECT_ROOT / "config.json"
SYSTEM_CONFIG_FILE: Path = PROJECT_ROOT / "config.system.json"

# ── Local cache dirs (nothing leaves the project folder) ─────────────────────
HF_CACHE_DIR:   Path = PROJECT_ROOT / ".hf_cache"
TORCH_HOME_DIR: Path = PROJECT_ROOT / ".cache" / "torch"

# Set env vars before any ML import — use setdefault so an explicit user
# export (e.g. HF_HOME in your shell) still takes precedence.
os.environ.setdefault("HF_HOME",              str(HF_CACHE_DIR))
os.environ.setdefault("HF_HUB_CACHE",         str(HF_CACHE_DIR / "hub"))
os.environ.setdefault("TORCH_HOME",           str(TORCH_HOME_DIR))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


# ── Config loaders ────────────────────────────────────────────────────────────

def load_config() -> dict:
    """Return the full parsed config.json (per-manga / per-run settings)."""
    if not CONFIG_FILE.exists():
        print(f"[ERROR] config.json not found at {CONFIG_FILE}")
        sys.exit(1)
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[ERROR] Invalid config.json: {exc}")
        sys.exit(1)


_warned_missing_system_config = False


def load_system_config() -> dict:
    """Return the full parsed config.system.json (system-wide settings).

    Falls back to an empty dict if the file is missing so callers can use
    .get() with their own defaults — avoids hard failures on first run.
    """
    global _warned_missing_system_config
    if not SYSTEM_CONFIG_FILE.exists():
        # Many helpers re-read the config; one warning per process is enough.
        if not _warned_missing_system_config:
            print(f"[WARN] config.system.json not found at {SYSTEM_CONFIG_FILE} — using defaults")
            _warned_missing_system_config = True
        return {}
    try:
        return json.loads(SYSTEM_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[ERROR] Invalid config.system.json: {exc}")
        sys.exit(1)


def load_download_config() -> dict:
    """Return merged download settings.

    Base defaults come from config.system.json → download_defaults.
    Per-manga overrides (manga_id, name, chapter) come from config.json → download.
    Project values always win over system defaults.
    """
    syscfg   = load_system_config()
    cfg      = load_config()
    defaults = syscfg.get("download_defaults", {})
    project  = cfg.get("download")
    if not project or not isinstance(project, dict):
        print("[ERROR] 'download' key missing in config.json")
        sys.exit(1)
    # Merge: defaults first, project values override
    merged = {**defaults, **project}
    return merged
