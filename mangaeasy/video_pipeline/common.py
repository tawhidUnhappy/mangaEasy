from __future__ import annotations

import os
import re
from pathlib import Path


DEFAULT_PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "content"))
DEFAULT_AUDIO_ROOT = Path(os.environ.get("AUDIO_ROOT", "audio"))
DEFAULT_OUTPUT_ROOT = Path(os.environ.get("OUTPUT_ROOT", "output"))
DEFAULT_WORK_DIR = Path(os.environ.get("WORK_DIR", "work"))
DEFAULT_KOKORO_ROOT = Path(os.environ.get("KOKORO_ROOT", "kokoro-82m"))

def project_name(project_root: Path, override: str | None = None) -> str:
    return override or project_root.resolve().name


def item_number(value: str) -> int:
    match = re.search(r"\d+", value)
    if not match:
        raise ValueError(f"Could not find a number in: {value}")
    return int(match.group(0))


def _format_item(number: int, width: int) -> str:
    return f"{number:0{width}d}"


def expand_item_tokens(tokens: list[str] | None, width: int = 2) -> list[str] | None:
    if not tokens:
        return None

    expanded: list[str] = []
    for raw_token in tokens:
        for token in (part.strip() for part in raw_token.split(",")):
            if not token:
                continue

            range_match = re.fullmatch(r"(\d+)\s*(?:-|\.\.|:)\s*(\d+)", token)
            if range_match:
                start = int(range_match.group(1))
                end = int(range_match.group(2))
                step = 1 if end >= start else -1
                expanded.extend(_format_item(number, width) for number in range(start, end + step, step))
                continue

            if token.isdigit():
                expanded.append(_format_item(int(token), width))
            else:
                expanded.append(token)

    seen: set[str] = set()
    deduped: list[str] = []
    for item in expanded:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def merge_item_selection(items: list[str] | None, item_range: str | None) -> list[str] | None:
    tokens: list[str] = []
    if items:
        tokens.extend(items)
    if item_range:
        tokens.append(item_range)
    return expand_item_tokens(tokens)


def _sort_key(path: Path) -> tuple[int, int, str]:
    has_number = any(ch.isdigit() for ch in path.name)
    number = item_number(path.name) if has_number else 10**9
    return (0 if has_number else 1, number, path.name.lower())


def item_dirs(root: Path, selected: list[str] | None = None) -> list[Path]:
    candidates = [
        path
        for path in root.iterdir()
        if path.is_dir()
        and not path.name.startswith(".")
        and ((path / "narration.json").exists() or (path / "panels").is_dir() or any(ch.isdigit() for ch in path.name))
    ]
    expanded = expand_item_tokens(selected)
    if expanded:
        wanted_names = {name.strip() for name in expanded}
        wanted_numbers = {item_number(name) for name in expanded if any(ch.isdigit() for ch in name)}
        candidates = [
            path
            for path in candidates
            if path.name in wanted_names
            or (any(ch.isdigit() for ch in path.name) and item_number(path.name) in wanted_numbers)
        ]
    return sorted(candidates, key=_sort_key)
