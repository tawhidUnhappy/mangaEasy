from __future__ import annotations

import os
import re
from pathlib import Path

from mangaeasy.utils import LazyArchiveRunDir, archive_into_run


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


def prune_recent_audio_for_resume(
    ordered_paths: list[Path], archive_run_dir: LazyArchiveRunDir, lookback: int = 5
) -> list[Path]:
    """Archive the in-progress audio file plus the previous N by narration order.

    ordered_paths is every expected audio file path in narration sequence order
    (across all selected items). "Current" is the first one not on disk yet —
    likely the file that was being written when the previous run stopped — or,
    if every file is already present, the last one in the sequence. Removing it
    and the lookback entries before it (so if current is index 5, previous is
    4, 3, 2, 1, 0) forces them to regenerate even though some still exist,
    instead of trusting file mtimes.

    Audio is expensive to regenerate, so files are always moved into
    archive_run_dir (under a subfolder named after their parent item folder)
    rather than deleted outright -- archive_run_dir only allocates its
    run_NNNN/ folder on first use, so a resume that finds nothing to prune
    never creates an empty one.
    """
    if not ordered_paths:
        return []
    current_idx = next((i for i, path in enumerate(ordered_paths) if not path.exists()), len(ordered_paths) - 1)
    start_idx = max(0, current_idx - lookback)
    removed = [path for path in ordered_paths[start_idx:current_idx + 1] if path.exists()]
    for path in removed:
        archive_into_run(path, archive_run_dir.dir, subdir=path.parent.name)
    return removed


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
