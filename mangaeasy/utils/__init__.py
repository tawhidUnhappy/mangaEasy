"""mangaeasy.utils — shared utilities."""

import json
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile


def numeric_sort_key(path: "Path | str") -> list:
    """Natural-sort key: extracts all integers from a filename stem.

    Works on Path objects or plain strings.  Files with no digits sort last.
    """
    stem = Path(path).stem
    nums = re.findall(r"\d+", stem)
    return [int(n) for n in nums] if nums else [float("inf")]


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
