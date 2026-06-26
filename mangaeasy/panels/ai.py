"""mangaeasy.panels.ai
AI-powered manga panel detection.

Model:
  - Panel detection : ragavsachdeva/magiv3  (MAGI v3, Florence2 architecture)

Pipeline:
  1. MAGI v3 detects panel bounding boxes.
  2. Boxes are sorted into manga reading order (right-to-left, top-to-bottom)
     using a band-based grouping algorithm.
"""

from __future__ import annotations

import os
import json
import subprocess
import tempfile
import warnings
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from PIL import Image

# Import config first — this sets HF_HOME before any HF library is imported.
from mangaeasy.config import HF_CACHE_DIR, PROJECT_ROOT
from mangaeasy.tools.external import python_command, resolve_tool_dir, tool_env

try:
    import torch
except ImportError:
    torch = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------


def _resolve_device(pref: str = "auto") -> str:
    if pref == "cpu":
        return "cpu"
    if pref == "auto":
        if torch is None:
            return "cpu"
        from mangaeasy.tools.external import resolve_device

        return resolve_device("auto")
    if pref == "cuda":
        if torch is None:
            return "cpu"
        return "cuda" if torch.cuda.is_available() else "cpu"
    return "cpu"


PANEL_DEVICE: str = _resolve_device(os.environ.get("PANEL_DEVICE", "auto"))

# ---------------------------------------------------------------------------
# MAGI v3  — lazy singleton
# ---------------------------------------------------------------------------

_magi_model = None
_magi_processor = None
_magi_initialized = False
MAGI_MODEL_ID = "ragavsachdeva/magiv3"
MAGI_V3_DIR = resolve_tool_dir("magi-v3", required=False)
USE_EXTERNAL_MAGI = os.environ.get("MANGAEASY_EXTERNAL_MAGI", "1") != "0"


def get_magi() -> Optional[object]:
    global _magi_model, _magi_processor, _magi_initialized
    if _magi_initialized:
        return _magi_model
    _magi_initialized = True
    if torch is None:
        print("[manga-ai] Torch is not installed; in-process MAGI is unavailable.")
        _magi_model = None
        return _magi_model
    try:
        from transformers import AutoModelForCausalLM, AutoProcessor  # type: ignore

        print("[manga-ai] Loading MAGI v3 …")
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning)
            _magi_processor = AutoProcessor.from_pretrained(
                MAGI_MODEL_ID, trust_remote_code=True
            )
            model = AutoModelForCausalLM.from_pretrained(
                MAGI_MODEL_ID,
                torch_dtype=torch.float16,
                trust_remote_code=True,
            )
        model = model.to(PANEL_DEVICE).eval()
        _magi_model = model
        print(f"[manga-ai] MAGI v3 ready on {PANEL_DEVICE.upper()}")
    except Exception as exc:
        print(f"[manga-ai] MAGI v3 unavailable: {exc}")
        _magi_model = None
    return _magi_model


# ---------------------------------------------------------------------------
# MAGI helpers
# ---------------------------------------------------------------------------


def _clamp_box(raw, W: int, H: int) -> Optional[Dict[str, int]]:
    try:
        x1, y1, x2, y2 = int(raw[0]), int(raw[1]), int(raw[2]), int(raw[3])
    except (TypeError, IndexError, ValueError):
        return None
    x1 = max(0, min(x1, W))
    y1 = max(0, min(y1, H))
    x2 = max(0, min(x2, W))
    y2 = max(0, min(y2, H))
    if x2 <= x1 or y2 <= y1:
        return None
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}


def _run_magi_external(image_path: Path) -> Dict:
    """Run MAGI in the standalone ../magi-v3 uv environment.

    This avoids the transformers version conflict between mangaEasy's main env
    and MAGI v3.
    """
    if MAGI_V3_DIR is None:
        print("[manga-ai] External MAGI not found. Put ./magi-v3 next to mangaEasy or set MAGI_V3_ROOT.")
        return {}

    script = MAGI_V3_DIR / "detect_magi.py"
    if not script.exists():
        print(f"[manga-ai] External MAGI script missing: {script}")
        return {}

    tmp_dir = PROJECT_ROOT / ".cache" / "magi-v3"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="magi_", suffix=".json", dir=tmp_dir, delete=False
    ) as tmp:
        out_path = Path(tmp.name)

    try:
        # Pass the raw env var (not the resolved PANEL_DEVICE) so the external
        # env can auto-detect its own CUDA even when the main env has CPU-only torch.
        device_arg = os.environ.get("PANEL_DEVICE", "auto")
        cmd = [
            *python_command(MAGI_V3_DIR),
            str(script),
            str(image_path),
            "--out", str(out_path),
            "--device", device_arg,
        ]
        proc = subprocess.run(cmd, cwd=MAGI_V3_DIR, env=tool_env(), capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            print("[manga-ai] External MAGI failed.")
            if proc.stdout.strip():
                print(proc.stdout.strip())
            if proc.stderr.strip():
                print(proc.stderr.strip())
            return {}
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        return payload.get("detections", {}) or {}
    except Exception as exc:
        print(f"[manga-ai] External MAGI error: {exc}")
        return {}
    finally:
        out_path.unlink(missing_ok=True)


def _run_magi(img_array: np.ndarray) -> Dict:
    model = get_magi()
    if model is None:
        return {}
    try:
        with torch.no_grad():
            results = model.predict_detections_and_associations(
                [img_array], _magi_processor
            )
        return results[0] if results else {}
    except Exception as exc:
        print(f"[manga-ai] MAGI inference error: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Reading-order sort
# ---------------------------------------------------------------------------


def _manga_reading_order(boxes: List[Dict[str, int]]) -> List[Dict[str, int]]:
    """Sort panels using a Topological Sort (DAG) to handle complex manga layouts."""
    if len(boxes) <= 1:
        return list(boxes)

    try:
        from mangaeasy.config import load_system_config

        syscfg = load_system_config()
        rtl = syscfg.get("cut_page", {}).get("reading_direction", "rtl") == "rtl"
    except Exception:
        rtl = True

    def cy(b: Dict) -> float:
        return (b["y1"] + b["y2"]) / 2.0

    def cx(b: Dict) -> float:
        return (b["x1"] + b["x2"]) / 2.0

    n = len(boxes)
    adj = {i: [] for i in range(n)}
    in_degree = {i: 0 for i in range(n)}

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            A, B = boxes[i], boxes[j]
            cyA, cyB = cy(A), cy(B)
            cxA, cxB = cx(A), cx(B)

            hA, hB = A["y2"] - A["y1"], B["y2"] - B["y1"]
            overlapY = max(0, min(A["y2"], B["y2"]) - max(A["y1"], B["y1"]))
            minH = min(hA, hB)

            if overlapY > 0.3 * minH:
                is_before = (cxA > cxB) if rtl else (cxA < cxB)
            else:
                is_before = cyA < cyB

            if is_before:
                adj[i].append(j)
                in_degree[j] += 1

    result = []
    visited = set()

    while len(result) < n:
        candidates = [i for i in range(n) if i not in visited and in_degree[i] == 0]

        if not candidates:
            unvisited = [i for i in range(n) if i not in visited]
            min_in = min(in_degree[i] for i in unvisited)
            candidates = [i for i in unvisited if in_degree[i] == min_in]

        def sort_key(idx: int):
            b = boxes[idx]
            _cy, _cx = cy(b), cx(b)
            return (int(_cy // 10), -_cx if rtl else _cx)

        candidates.sort(key=sort_key)
        best = candidates[0]

        visited.add(best)
        result.append(boxes[best])

        for neighbor in adj[best]:
            in_degree[neighbor] -= 1

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_panels_ai(image_path: Path) -> List[Dict[str, int]]:
    """Detect manga panels with MAGI v3.

    Returns [{x1,y1,x2,y2}] sorted in manga reading order
    (right-to-left within horizontal bands, bands top-to-bottom).
    """
    img = Image.open(image_path).convert("RGB")
    W, H = img.size
    if USE_EXTERNAL_MAGI:
        raw = _run_magi_external(image_path)
    else:
        raw = _run_magi(np.array(img, dtype=np.uint8))
    if not raw:
        return []
    boxes = [b for entry in raw.get("panels", []) if (b := _clamp_box(entry, W, H))]
    return _manga_reading_order(boxes)


# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys as _sys

    if len(_sys.argv) < 2:
        print("Usage: python -m mangaeasy.panels.ai <image_path>")
        _sys.exit(1)
    path = Path(_sys.argv[1])
    print("\n=== Panel detection ===")
    for i, p in enumerate(detect_panels_ai(path)):
        print(f"  Panel {i+1}: {p}")
