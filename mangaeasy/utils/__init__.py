"""mangaeasy.utils — shared utilities."""

import json
import os
import re
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile


def emit_result(**payload) -> None:
    """Print the machine-parsable success marker line.

    Generation commands end a successful run with exactly one
    ``MANGAEASY_RESULT {...json...}`` line so scripts and AI agents can find
    the produced files without scraping human log text — same family as the
    ``MANGAEASY_PROGRESS n/m`` and ``MANGAEASY_OPEN_URL`` markers. Keep the
    payload JSON on a single line; Paths are stringified automatically.
    """
    print("MANGAEASY_RESULT " + json.dumps(payload, ensure_ascii=False, default=str), flush=True)


def numeric_sort_key(path: "Path | str") -> list:
    """Natural-sort key: extracts all integers from a filename stem.

    Works on Path objects or plain strings.  Files with no digits sort last.
    """
    stem = Path(path).stem
    nums = re.findall(r"\d+", stem)
    return [int(n) for n in nums] if nums else [float("inf")]


def next_archive_run_dir(old_root: Path) -> Path:
    """Allocate and create the next unused old_root/run_NNNN/ folder.

    Scans whatever run_NNNN folders already exist so any number of past runs
    can stack up without colliding or clobbering each other.
    """
    old_root.mkdir(parents=True, exist_ok=True)
    existing = [
        int(match.group(1))
        for entry in old_root.iterdir()
        if entry.is_dir() and (match := re.fullmatch(r"run_(\d+)", entry.name))
    ]
    next_run = (max(existing) + 1) if existing else 1
    run_dir = old_root / f"run_{next_run:04d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def archive_into_run(path: Path, run_dir: Path, *, subdir: str | None = None) -> Path | None:
    """Move an existing file into run_dir (optionally nested under subdir), preserving its name."""
    if not path.exists():
        return None
    destination_dir = (run_dir / subdir) if subdir else run_dir
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / path.name
    shutil.move(str(path), str(destination))
    return destination


class LazyArchiveRunDir:
    """Allocates a single run_NNNN/ folder the first time it's actually needed.

    Audio generation may run for many items without ever overwriting an
    existing file, so eagerly creating an empty old/run_NNNN/ on every
    invocation would litter the project with empty runs. This defers
    `next_archive_run_dir` until the first archive actually happens, then
    reuses that same folder for the rest of the run.
    """

    def __init__(self, old_root: Path) -> None:
        self._old_root = old_root
        self._dir: Path | None = None

    @property
    def dir(self) -> Path:
        if self._dir is None:
            self._dir = next_archive_run_dir(self._old_root)
        return self._dir

    @property
    def allocated(self) -> Path | None:
        return self._dir


def archive_before_overwrite(path: Path) -> Path | None:
    """Move an existing output file into <path's folder>/old/run_NNNN/ before it gets overwritten.

    Re-running a generation step would otherwise silently replace the last
    result. Each call allocates its own run_NNNN folder, which is right for a
    single output file (item video, long video, chapter video) generated
    once per invocation.
    """
    if not path.exists():
        return None
    run_dir = next_archive_run_dir(path.parent / "old")
    return archive_into_run(path, run_dir)


def atomic_write_json(path: Path, data: "dict | list") -> bool:
    """Write data as JSON to path atomically (tmp + rename)."""
    try:
        with NamedTemporaryFile("w", dir=str(path.parent), delete=False, encoding="utf-8") as tf:
            json.dump(data, tf, indent=2, ensure_ascii=False)
            tmpname = tf.name
        os.replace(tmpname, str(path))
        return True
    except Exception as exc:
        print(f"[error] Failed to write config: {exc}")
        try:
            if "tmpname" in locals():
                os.remove(tmpname)
        except Exception:
            pass
        return False
