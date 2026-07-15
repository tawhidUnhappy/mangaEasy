"""mangaeasy.series_plan — plan and track fixed-size upload batches.

A long series ships to YouTube in fixed windows of items — chapters 01–12,
then 13–24, and so on (12 per video by default). Two commands own that state:

- ``mangaeasy series-plan`` answers "which batch is next?": it slices the
  project's item folders into stable windows, checks each item's readiness
  (panels, narration), and cross-references ``publish.json`` to mark batches
  already uploaded. Machine-readable with ``--json``.
- ``mangaeasy series-mark-published`` records a batch into
  ``library/<project>/publish.json`` after a successful ``youtube-upload``
  (video id, title, timestamp). Re-recording the same item set replaces the
  old record instead of duplicating it.

``publish.json`` lives next to ``manga.json`` at the project root and is
machine-managed — don't hand-edit it mid-run.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from mangaeasy.brand import CLI_NAME

DEFAULT_BATCH_SIZE = 12
_PUBLISH_FILE = "publish.json"


def load_publish_json(project_root: Path) -> dict:
    path = project_root / _PUBLISH_FILE
    if not path.exists():
        return {"published": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"invalid {path}: {exc}") from exc
    data.setdefault("published", [])
    return data


def save_publish_json(project_root: Path, data: dict) -> Path:
    path = project_root / _PUBLISH_FILE
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def item_readiness(item_dir: Path) -> dict:
    """What this item has so far (source panels + narration only — generated
    audio/video live under separate roots; use video-validate for those)."""
    panels_dir = item_dir / "panels"
    from mangaeasy.video_pipeline.common import IMAGE_EXTENSIONS

    panels = (
        sum(1 for p in panels_dir.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS)
        if panels_dir.is_dir() else 0
    )
    downloads = item_dir / "download"
    return {
        "item": item_dir.name,
        "downloaded": downloads.is_dir() and any(downloads.iterdir()),
        "panels": panels,
        "narration": (item_dir / "narration.json").is_file(),
    }


def build_plan(project_root: Path, batch_size: int) -> dict:
    from mangaeasy.video_pipeline.common import item_dirs

    items = item_dirs(project_root)
    publish = load_publish_json(project_root)
    published_sets = {tuple(rec.get("items", [])): rec for rec in publish["published"]}

    batches = []
    next_batch = None
    for start in range(0, len(items), batch_size):
        window = items[start:start + batch_size]
        names = [d.name for d in window]
        record = published_sets.get(tuple(names))
        readiness = [item_readiness(d) for d in window]
        ready = all(r["panels"] > 0 and r["narration"] for r in readiness)
        batch = {
            "batch": f"{names[0]}-{names[-1]}",
            "items": names,
            "full": len(names) == batch_size,
            "published": record is not None,
            "video_id": record.get("video_id") if record else None,
            "ready_to_render": ready,
            "readiness": readiness,
        }
        batches.append(batch)
        if next_batch is None and record is None:
            next_batch = batch

    return {
        "project": str(project_root),
        "batch_size": batch_size,
        "items_total": len(items),
        "batches": batches,
        "next_batch": next_batch,
        "note": None if (next_batch is None or next_batch["full"]) else (
            "next_batch is a partial window — either the series has ended or "
            f"later chapters aren't downloaded yet (run `{CLI_NAME} download --all`)."
        ),
    }


def plan_main() -> int:
    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} series-plan",
        description="Slice a project's items into fixed upload batches (12 per "
                    "video by default), report readiness and published state, "
                    "and name the next batch to produce.",
    )
    parser.add_argument("--project-root", type=Path, required=True,
                        help="Project folder (library/<name>).")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--json", action="store_true", help="Emit one JSON object on stdout.")
    args = parser.parse_args()

    if not args.project_root.is_dir():
        print(f"ERROR: project root not found: {args.project_root}", file=sys.stderr)
        return 1
    try:
        plan = build_plan(args.project_root, args.batch_size)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(plan, ensure_ascii=False))
        return 0

    print(f"series-plan: {plan['project']} — {plan['items_total']} item(s), "
          f"{args.batch_size} per video\n")
    for batch in plan["batches"]:
        if batch["published"]:
            status = f"published (video {batch['video_id'] or '?'})"
        elif batch["ready_to_render"]:
            status = "ready to render"
        else:
            todo = [r["item"] for r in batch["readiness"]
                    if not (r["panels"] and r["narration"])]
            status = f"needs work: {', '.join(todo)}"
        partial = "" if batch["full"] else "  [partial]"
        print(f"  {batch['batch']}: {status}{partial}")
    if plan["next_batch"]:
        print(f"\nNext batch: {plan['next_batch']['batch']}")
        if plan["note"]:
            print(f"Note: {plan['note']}")
    else:
        print("\nAll batches published — nothing to do.")
    return 0


def mark_main() -> int:
    from mangaeasy.video_pipeline.common import item_dirs, merge_item_selection

    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} series-mark-published",
        description="Record an uploaded batch in library/<project>/publish.json "
                    "so series-plan advances to the next window.",
    )
    parser.add_argument("--project-root", type=Path, required=True,
                        help="Project folder (library/<name>).")
    parser.add_argument("--items", nargs="+", required=True,
                        help="The batch's items, e.g. 01-12 or 01 02 ... 12.")
    parser.add_argument("--video-id", required=True, help="YouTube video id from youtube-upload.")
    parser.add_argument("--title", default=None, help="The uploaded video's title (optional).")
    parser.add_argument("--url", default=None, help="Watch URL (optional; derived from id if omitted).")
    args = parser.parse_args()

    selection = merge_item_selection(args.items, None)
    matched = item_dirs(args.project_root, selection)
    if not matched:
        print(f"ERROR: no items matching {args.items} under {args.project_root}",
              file=sys.stderr)
        return 1
    names = [d.name for d in matched]

    try:
        publish = load_publish_json(args.project_root)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    record = {
        "items": names,
        "video_id": args.video_id,
        "url": args.url or f"https://www.youtube.com/watch?v={args.video_id}",
        "title": args.title,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    replaced = [rec for rec in publish["published"] if rec.get("items") == names]
    publish["published"] = [rec for rec in publish["published"] if rec.get("items") != names]
    publish["published"].append(record)
    path = save_publish_json(args.project_root, publish)
    verb = "replaced" if replaced else "recorded"
    print(f"[info] batch {names[0]}-{names[-1]} {verb} as published "
          f"(video {args.video_id}) → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(plan_main())
