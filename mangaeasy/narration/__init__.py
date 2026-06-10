"""mangaeasy.narration — narration JSON helpers."""

import json
from pathlib import Path

from mangaeasy.utils import atomic_write_json


def load_narration(path: Path) -> list:
    """Load a narration.json file and return the list of entries.

    Returns [] if the file does not exist.  Wraps a bare dict in a list
    (legacy single-entry files).
    """
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return [data]
    return data if isinstance(data, list) else []


def save_narration(data: list, path: Path) -> None:
    """Atomically write a narration entry list back to disk."""
    atomic_write_json(path, data)
