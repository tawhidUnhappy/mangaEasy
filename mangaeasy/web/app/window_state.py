"""Persist and restore the pywebview window geometry between sessions."""
from __future__ import annotations

import json
from pathlib import Path

_STATE_FILE = Path.home() / ".mangaeasy" / "window_state.json"

_DEFAULTS = {"width": 1240, "height": 820, "x": None, "y": None, "maximized": False}


def load() -> dict:
    try:
        if _STATE_FILE.exists():
            data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {**_DEFAULTS, **data}
    except Exception:
        pass
    return dict(_DEFAULTS)


def save(win) -> None:
    try:
        maximized = bool(getattr(win, "maximized", False))
        data = {
            "maximized": maximized,
            # When maximized the reported x/y/w/h are the screen dimensions —
            # save them only when restored so next non-maximized launch uses
            # sensible values.
            "width":  win.width  if not maximized else _DEFAULTS["width"],
            "height": win.height if not maximized else _DEFAULTS["height"],
            "x":      win.x      if not maximized else None,
            "y":      win.y      if not maximized else None,
        }
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass
