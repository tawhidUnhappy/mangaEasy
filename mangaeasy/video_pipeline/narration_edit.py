"""mangaeasy.video_pipeline.narration_edit — edit narration entries from the CLI.

``mangaeasy narration-edit`` upserts, deletes and lists entries in an item's
``narration.json`` (or ``intro.json`` with ``--intro``) so an agent fixes a
line with one command instead of hand-editing JSON:

    mangaeasy narration-edit --project-root library/<P> --item 01 \
        --set ch01_005.jpg "Something ancient stirs inside the light." \
        --delete ch01_010.jpg --prune-audio

- New images are inserted at their name-sorted position (panel filenames are
  zero-padded, so name order == reading order; hook copies sort first, the
  CTA copy last — the naming convention is load-bearing).
- ``--prune-audio`` deletes the WAV of every entry whose text changed or was
  removed, so the next audio run regenerates exactly those (same contract as
  ``video-audio-audit --fix``).
- ``--batch FILE`` upserts a whole JSON array (``[{"image", "narration"}]``)
  in one call — initial authoring stays a single file write, edits stay CLI.
- Every write archives the previous file first and re-checks speakability
  and image existence, so a bad edit is caught immediately.

This module edits the raw files; *reading* narration for playback must keep
going through ``item_assets.load_narration()`` (which merges intro.json).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from mangaeasy.brand import CLI_NAME
from mangaeasy.utils import archive_before_overwrite, emit_result
from mangaeasy.video_pipeline.check_items import is_speakable


def sorted_insert_position(entries: list[dict], image: str) -> int:
    """Index where a new image belongs, assuming name-sorted reading order."""
    for i, entry in enumerate(entries):
        if entry.get("image", "") > image:
            return i
    return len(entries)


def upsert(entries: list[dict], image: str, text: str) -> tuple[list[dict], str | None]:
    """Set image's narration; returns (entries, previous_text or None)."""
    for entry in entries:
        if entry.get("image") == image:
            previous = entry.get("narration", "")
            entry["narration"] = text
            return entries, previous
    entries.insert(sorted_insert_position(entries, image),
                   {"image": image, "narration": text})
    return entries, None


def parse_args() -> argparse.Namespace:
    from mangaeasy.video_pipeline.common import DEFAULT_AUDIO_ROOT, DEFAULT_PROJECT_ROOT

    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} narration-edit",
        description="Upsert/delete/list narration.json (or intro.json) entries "
                    "without hand-editing JSON.",
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--item", required=True, help="Item folder, e.g. 01.")
    parser.add_argument("--intro", action="store_true",
                        help="Edit intro.json instead of narration.json.")
    parser.add_argument("--set", nargs=2, action="append", default=[],
                        metavar=("IMAGE", "TEXT"),
                        help="Set IMAGE's narration to TEXT (add or replace). Repeatable.")
    parser.add_argument("--delete", action="append", default=[], metavar="IMAGE",
                        help="Remove IMAGE's entry. Repeatable.")
    parser.add_argument("--batch", type=Path, default=None,
                        help="Upsert every entry from a JSON array file "
                             '([{"image", "narration"}]).')
    parser.add_argument("--set-json", default=None, metavar="JSON",
                        help="Upsert entries from an inline JSON array (same shape as "
                             "--batch, no file needed — the MCP-friendly form).")
    parser.add_argument("--list", dest="do_list", action="store_true",
                        help="Print the entries (index, image, text) after any edits.")
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    parser.add_argument("--prune-audio", action="store_true",
                        help="Delete the WAV of every changed/removed entry so the next "
                             "audio run regenerates it.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.item) or args.item in {".", ".."}:
        print("ERROR: --item must be one direct-child folder name (letters, digits, dot, underscore, hyphen)")
        return 1
    item_dir = (project_root / args.item).resolve()
    if item_dir.parent != project_root:
        print(f"ERROR: --item escapes project root: {args.item}")
        return 1
    if not item_dir.is_dir():
        print(f"ERROR: no item folder {item_dir}")
        return 1
    target = item_dir / ("intro.json" if args.intro else "narration.json")

    entries: list[dict] = []
    if target.is_file():
        entries = json.loads(target.read_text(encoding="utf-8-sig"))

    batch: list[tuple[str, str]] = [(image, text) for image, text in args.set]
    if args.batch is not None:
        for entry in json.loads(args.batch.read_text(encoding="utf-8-sig")):
            batch.append((entry["image"], entry["narration"]))
    if args.set_json is not None:
        for entry in json.loads(args.set_json):
            batch.append((entry["image"], entry["narration"]))

    stale_stems: list[str] = []
    added = replaced = removed = 0
    for image, text in batch:
        entries, previous = upsert(entries, image, text)
        if previous is None:
            added += 1
        elif previous != text:
            replaced += 1
            stale_stems.append(Path(image).stem)
    for image in args.delete:
        before = len(entries)
        entries = [e for e in entries if e.get("image") != image]
        if len(entries) == before:
            print(f"[warn] --delete {image}: no such entry")
        else:
            removed += 1
            stale_stems.append(Path(image).stem)

    changed = bool(batch or args.delete)
    if changed:
        archived = archive_before_overwrite(target)
        if archived:
            print(f"[info] previous file archived: {archived}")
        target.write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
        print(f"{target.name}: +{added} added, {replaced} replaced, {removed} removed "
              f"({len(entries)} entries)")

    pruned = []
    if args.prune_audio and stale_stems:
        audio_dir = args.audio_root.resolve() / project_root.name / args.item
        for stem in stale_stems:
            wav = audio_dir / f"{stem}.wav"
            if wav.exists():
                wav.unlink()
                pruned.append(wav.name)
                print(f"[prune] deleted stale audio: {wav}")
        if pruned:
            print("re-run audio generation to regenerate exactly those files.")

    problems = []
    for entry in entries:
        image = entry.get("image", "")
        if not (item_dir / "panels" / image).is_file():
            problems.append(f"missing panel image: {image}")
        if not is_speakable(entry.get("narration", "")):
            problems.append(f"unspeakable text (no letters/digits): {image}")
    for problem in problems:
        print(f"[warn] {problem}")

    if args.do_list or not changed:
        for i, entry in enumerate(entries):
            text = (entry.get("narration", "") or "").replace("\n", " ")
            print(f"{i:3d}  {entry.get('image', '?'):<28} {text[:90]}")

    emit_result(command="narration-edit", file=target, entries=len(entries),
                added=added, replaced=replaced, removed=removed,
                pruned_audio=pruned, warnings=problems)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
