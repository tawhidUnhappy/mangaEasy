from __future__ import annotations

import os
import re
from pathlib import Path

from mangaeasy.path_safety import validate_portable_segment
from mangaeasy.utils import LazyArchiveRunDir, archive_into_run


def _env_path(namespaced: str, legacy: str, default: str) -> Path:
    """Flag-default roots: MANGAEASY_* wins, bare legacy name still honoured.

    The unprefixed names (AUDIO_ROOT, WORK_DIR, ...) are generic enough to
    collide with unrelated software in a user's shell; new setups should use
    the MANGAEASY_-prefixed forms. Agents should pass explicit --*-root flags
    and rely on neither.
    """
    value = os.environ.get(namespaced) or os.environ.get(legacy)
    return Path(value) if value else Path(default)


DEFAULT_PROJECT_ROOT = _env_path("MANGAEASY_ITEMS_ROOT", "PROJECT_ROOT", "content")
DEFAULT_AUDIO_ROOT = _env_path("MANGAEASY_AUDIO_ROOT", "AUDIO_ROOT", "audio")
DEFAULT_OUTPUT_ROOT = _env_path("MANGAEASY_OUTPUT_ROOT", "OUTPUT_ROOT", "output")
DEFAULT_WORK_DIR = _env_path("MANGAEASY_WORK_DIR", "WORK_DIR", "work")
DEFAULT_KOKORO_ROOT = Path(os.environ.get("KOKORO_ROOT", "kokoro-82m"))

# The one home for media-extension sets — modules used to keep drifting
# private copies (one even counted .gif as a panel; the renderer doesn't).
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac"}

# Above 4 concurrent CUDA contexts, consumer NVIDIA cards crash under
# multi-process TTS (confirmed on an RTX 3060 in production: 4 stable, 8
# crashes even with cudnn.benchmark off). Enforced in code because "keep it
# ≤ 4" lived only in the docs, where an agent that skipped the docs could
# not see it.
GPU_WORKERS_SAFE_MAX = 4


def clamp_gpu_workers(requested: int) -> int:
    """Clamp --gpu-workers to the known-safe ceiling (warn, don't crash).

    MANGAEASY_UNSAFE_GPU_WORKERS=1 opts out for hardware that has actually
    been tested higher.
    """
    import sys

    if requested <= GPU_WORKERS_SAFE_MAX:
        return max(1, requested)
    if os.environ.get("MANGAEASY_UNSAFE_GPU_WORKERS") == "1":
        print(f"[warn] --gpu-workers {requested} exceeds the tested-safe maximum "
              f"{GPU_WORKERS_SAFE_MAX}; proceeding because MANGAEASY_UNSAFE_GPU_WORKERS=1.",
              file=sys.stderr)
        return requested
    print(f"[warn] --gpu-workers {requested} clamped to {GPU_WORKERS_SAFE_MAX}: more "
          f"concurrent CUDA contexts than this crashes consumer GPUs "
          f"(set MANGAEASY_UNSAFE_GPU_WORKERS=1 to override on tested hardware).",
          file=sys.stderr)
    return GPU_WORKERS_SAFE_MAX

def project_name(project_root: Path, override: str | None = None) -> str:
    value = override if override is not None else project_root.resolve().name
    return validate_portable_segment(value, label="project name")


def item_number(value: str) -> int:
    match = re.search(r"\d+", value)
    if not match:
        raise ValueError(f"Could not find a number in: {value}")
    return int(match.group(0))


def item_value(value: str) -> float:
    """The item's full numeric value: "02" -> 2.0, "2.1" -> 2.1, "9.5" -> 9.5.

    Selection, sorting, and join discovery all compare THIS, never
    ``item_number`` — the first-integer parse made "2.1" collide with "02"
    (``--items 02`` used to select 2.1/2.2 too, and split chapters silently
    shadowed their integer sibling)."""
    match = re.search(r"\d+(?:\.\d+)?", value)
    if not match:
        raise ValueError(f"Could not find a number in: {value}")
    return float(match.group(0))


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


def _sort_key(path: Path) -> tuple[int, float, str]:
    has_number = any(ch.isdigit() for ch in path.name)
    number = item_value(path.name) if has_number else float(10**9)
    return (0 if has_number else 1, number, path.name.lower())


def chunk_list(items: list, shards: int) -> list[list]:
    """Split into `shards` roughly-equal, contiguous, non-empty chunks (fewer if too small)."""
    if shards <= 1 or len(items) <= 1:
        return [items]
    size = -(-len(items) // shards)  # ceil division
    return [items[i:i + size] for i in range(0, len(items), size)]


def _prune_recent_audio_in_sequence(
    ordered_paths: list[Path], archive_run_dir: LazyArchiveRunDir, lookback: int
) -> list[Path]:
    if not ordered_paths:
        return []
    current_idx = next((i for i, path in enumerate(ordered_paths) if not path.exists()), len(ordered_paths) - 1)
    start_idx = max(0, current_idx - lookback)
    removed = [path for path in ordered_paths[start_idx:current_idx + 1] if path.exists()]
    for path in removed:
        archive_into_run(path, archive_run_dir.dir, subdir=path.parent.name)
    return removed


def prune_recent_audio_for_resume(
    ordered_paths: list[Path],
    archive_run_dir: LazyArchiveRunDir,
    lookback: int = 5,
    shards: int = 1,
) -> list[Path]:
    """Archive the in-progress audio file(s) plus the previous N by narration order.

    ordered_paths is every expected audio file path in narration sequence order
    (across all selected items). "Current" is the first one not on disk yet —
    likely the file that was being written when the previous run stopped — or,
    if every file is already present, the last one in the sequence. Removing it
    and the lookback entries before it (so if current is index 5, previous is
    4, 3, 2, 1, 0) forces them to regenerate even though some still exist,
    instead of trusting file mtimes.

    With --gpu-workers > 1, generation splits ordered_paths into that many
    contiguous shards (matching shard_manifest's split) and runs them in
    parallel -- so there isn't one in-progress file at the moment of
    interruption, there's one per worker, each at a different position in
    its own shard. Treating the whole list as a single sequence would only
    find the earliest shard's boundary and miss the rest, which already
    "exist" on disk past that point. Pass the same shard count used for
    generation so each shard's own boundary gets checked independently.

    Audio is expensive to regenerate, so files are always moved into
    archive_run_dir (under a subfolder named after their parent item folder)
    rather than deleted outright -- archive_run_dir only allocates its
    run_NNNN/ folder on first use, so a resume that finds nothing to prune
    never creates an empty one.
    """
    removed: list[Path] = []
    for chunk in chunk_list(ordered_paths, shards):
        removed.extend(_prune_recent_audio_in_sequence(chunk, archive_run_dir, lookback))
    return removed


def find_latest_long_video(output_root: Path, name: str) -> Path | None:
    """Most recently created plain-join long video for a project, or None.

    Joining no longer overwrites one fixed filename -- every run of
    video-join writes its own timestamped file (see
    long_video_builder.output_path), so steps that act on "the" long video
    after the fact (video-add-bgm, video-normalize-audio, video-validate)
    need to find the latest one instead of assuming a name. Excludes
    background-music mixes (named with a "_bgm_" segment) so add-bgm's
    default input is always a clean narration-only join, never a previous
    mix, and never looks inside old/ (archived/superseded files).
    """
    project_dir = output_root.resolve() / name
    if not project_dir.is_dir():
        return None
    candidates = [
        path for path in project_dir.glob(f"{name}_full*.mp4")
        if path.is_file() and "_bgm_" not in path.name
        and ".before_normalize" not in path.name
    ]
    if not candidates:
        return None
    # Timestamped joins (name_full_<stamp>.mp4) are the current format; a
    # bare legacy name_full.mp4 from an old run must never shadow them just
    # because something touched its mtime.
    timestamped = [p for p in candidates if re.fullmatch(rf"{re.escape(name)}_full_[\d-]+\.mp4", p.name)]
    pool = timestamped or candidates
    return max(pool, key=lambda path: path.stat().st_mtime)


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
        # Match by exact name or exact numeric VALUE ("05" selects 05 or 5,
        # never 5.5) — split chapters like 2.1 must be named explicitly
        # (`--items 2.1`); an integer token never drags decimals along.
        wanted_values = {item_value(name) for name in expanded if any(ch.isdigit() for ch in name)}
        candidates = [
            path
            for path in candidates
            if path.name in wanted_names
            or (any(ch.isdigit() for ch in path.name) and item_value(path.name) in wanted_values)
        ]
    return sorted(candidates, key=_sort_key)
