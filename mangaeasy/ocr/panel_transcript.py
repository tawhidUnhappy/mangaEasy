"""mangaeasy.ocr.panel_transcript — OCR every panel BEFORE narration exists.

``mangaeasy panel-transcript`` writes ``<item>/transcript.json`` — one entry
per panel image, each carrying an ``ocr`` field with the bubble/caption text
DeepSeek-OCR 2 read off that panel. It exists to *ground* narration writing:

- the narration author works from panel image + transcript, so quoted or
  paraphrased dialogue stays anchored to what the bubbles actually say
  (prevents unnatural paraphrase drift);
- speaker attribution is checked against explicit text instead of memory of
  a 500-panel read-through;
- ``narration-review-sheets`` shows the transcript next to each narration
  line for the verification pass.

Under the hood it seeds the transcript files (preserving existing ``ocr``
values) and runs the existing ``deepseek-ocr2`` command over them in one
subprocess, so the model loads once for all items. Requires the
``deepseek-ocr2`` tool env (``mangaeasy install-tool deepseek-ocr2``).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from mangaeasy.runtime import cli_command
from mangaeasy.utils import emit_result

PANEL_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def seed_transcript(item_dir: Path) -> tuple[Path, int, int]:
    """Write/refresh transcript.json listing every panel; keep existing ocr."""
    panels = sorted(
        (p.name for p in (item_dir / "panels").iterdir()
         if p.suffix.lower() in PANEL_EXTENSIONS),
    )
    path = item_dir / "transcript.json"
    existing: dict[str, dict] = {}
    if path.is_file():
        try:
            for entry in json.loads(path.read_text(encoding="utf-8-sig")):
                if isinstance(entry, dict) and entry.get("image"):
                    existing[entry["image"]] = entry
        except Exception:
            print(f"[{item_dir.name}] unreadable transcript.json — rebuilding")
    entries = [existing.get(name, {"image": name}) for name in panels]
    dropped = len(set(existing) - set(panels))
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    return path, len(entries), dropped


def coverage(path: Path) -> tuple[int, int]:
    entries = json.loads(path.read_text(encoding="utf-8-sig"))
    done = sum(1 for e in entries if (e.get("ocr") or "").strip())
    return done, len(entries)


def parse_args() -> argparse.Namespace:
    from mangaeasy.video_pipeline.common import DEFAULT_PROJECT_ROOT

    parser = argparse.ArgumentParser(
        prog="mangaeasy panel-transcript",
        description="OCR every panel into <item>/transcript.json (DeepSeek-OCR 2) to "
                    "ground narration writing and speaker attribution.",
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--items", nargs="*")
    parser.add_argument("--item-range")
    parser.add_argument("--force", action="store_true",
                        help="Re-OCR panels that already have an ocr value.")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--seed-only", action="store_true",
                        help="Only (re)write the transcript skeletons; skip the OCR run.")
    parser.add_argument("--respect-claims", action="store_true",
                        help="Abort (exit 1) if another live agent's workboard claim covers any "
                             "selected item at this stage (see docs/multi-agent.md).")
    parser.add_argument("--agent", default=None,
                        help="This agent's identity for --respect-claims "
                             "(default: $MANGAEASY_AGENT or user@host).")
    return parser.parse_args()


def main() -> int:
    from mangaeasy.video_pipeline.common import item_dirs, merge_item_selection

    args = parse_args()
    if args.respect_claims:
        from mangaeasy.workboard import respect_claims_gate

        if not respect_claims_gate(args.project_root, args.items, args.item_range, ("transcribe",), args.agent):
            return 1
    project_root = args.project_root.resolve()
    selected = item_dirs(project_root, merge_item_selection(args.items, args.item_range))
    if not selected:
        print(f"[FATAL] No item folders found under {project_root}")
        return 1

    transcripts: list[Path] = []
    for item_dir in selected:
        if not (item_dir / "panels").is_dir():
            print(f"[{item_dir.name}] no panels dir — skipped")
            continue
        path, count, dropped = seed_transcript(item_dir)
        transcripts.append(path)
        print(f"[{item_dir.name}] transcript seeded: {count} panel(s)"
              + (f", {dropped} stale entr(ies) dropped" if dropped else ""), flush=True)
    if not transcripts:
        print("[FATAL] nothing to transcribe")
        return 1

    if not args.seed_only:
        cmd = cli_command(
            "deepseek-ocr2",
            "--project-root", str(project_root),
            "--device", args.device,
        )
        for t in transcripts:
            cmd += ["--narration", str(t)]
        if args.force:
            cmd.append("--force")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print("[FATAL] deepseek-ocr2 run failed — transcripts are seeded, re-run to resume")
            return 1

    items = {}
    for t in transcripts:
        done, total = coverage(t)
        items[t.parent.name] = {"transcript": str(t), "ocr_done": done, "panels": total}
        print(f"[{t.parent.name}] ocr coverage: {done}/{total}", flush=True)
    emit_result(command="panel-transcript", items=items)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
