"""mangaeasy.video_pipeline.narration_check — structural narration validation.

``mangaeasy narration-check`` verifies the *shape* of each item's narration
before audio generation: files parse, every entry references a panel image
that exists, every panel image has an entry, and no narration is empty. It
covers ``intro.json`` too (checked separately, since its errors need fixing
in a different file) — and flags any panel listed in *both* ``intro.json`` and
``narration.json``, because the intro is prepended at render time so such a
panel plays twice (the cold-open replays a beat that then shows again
in-context).

This is the machine half of narration verification. The semantic half — is
the narration faithful to the panels, is dialogue attributed to the right
speaker — cannot be checked structurally; an agent does that by reading the
panels against the text (see docs/operate/crop-verify-narrate.md).

Exit code 0 = every checked item is clean; 1 = at least one problem.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})


def _check_entries(entries, panels_dir: Path, label: str, problems: list[str]) -> list[str]:
    """Validate one file's entry list; return the images it references, in order."""
    images: list[str] = []
    if not isinstance(entries, list):
        problems.append(f"{label}: must be a JSON array")
        return images
    for idx, entry in enumerate(entries):
        where = f"{label}[{idx}]"
        if not isinstance(entry, dict):
            problems.append(f"{where}: entry is not an object")
            continue
        image = entry.get("image")
        narration = entry.get("narration")
        if not isinstance(image, str) or not image:
            problems.append(f"{where}: missing/empty 'image'")
        else:
            images.append(image)
            if not (panels_dir / image).is_file():
                problems.append(f"{where}: image '{image}' not found in panels/")
        if not isinstance(narration, str) or not narration.strip():
            problems.append(f"{where}: missing/empty 'narration'"
                            + (f" (image '{image}')" if isinstance(image, str) else ""))
    return images


def check_item(item_dir: Path) -> dict:
    """Structural report for one item; 'problems' empty means clean."""
    panels_dir = item_dir / "panels"
    problems: list[str] = []
    narration_images: list[str] = []
    intro_images: list[str] = []

    narration_path = item_dir / "narration.json"
    if not narration_path.is_file():
        problems.append("narration.json missing")
    else:
        try:
            data = json.loads(narration_path.read_text(encoding="utf-8-sig"))
            narration_images = _check_entries(data, panels_dir, "narration.json", problems)
        except Exception as exc:
            problems.append(f"narration.json: invalid JSON ({exc})")

    intro_path = item_dir / "intro.json"
    if intro_path.is_file():
        try:
            data = json.loads(intro_path.read_text(encoding="utf-8-sig"))
            intro_images = _check_entries(data, panels_dir, "intro.json", problems)
        except Exception as exc:
            problems.append(f"intro.json: invalid JSON ({exc})")

    # intro.json is prepended before narration.json at render time, so any
    # panel listed in both plays twice — a cold-open that silently replays a
    # beat that then shows again in-context. Almost always an authoring slip;
    # the cold open should use panels the chapter's narration.json omits.
    narration_set = set(narration_images)
    overlap = [img for img in dict.fromkeys(intro_images) if img in narration_set]
    if overlap:
        problems.append(
            f"{len(overlap)} panel(s) are in both intro.json and narration.json and "
            "will render twice (the cold-open replays them; give the intro panels the "
            "chapter's narration.json does not use): "
            + ", ".join(overlap[:5]) + ("…" if len(overlap) > 5 else ""))

    entry_count = len(narration_images) + len(intro_images)
    covered = narration_set | set(intro_images)
    uncovered: list[str] = []
    if panels_dir.is_dir():
        panel_names = sorted(p.name for p in panels_dir.iterdir()
                             if p.suffix.lower() in _IMAGE_EXTS)
        uncovered = [name for name in panel_names if name not in covered]
        if uncovered:
            problems.append(f"{len(uncovered)} panel image(s) have no narration entry: "
                            + ", ".join(uncovered[:5])
                            + ("…" if len(uncovered) > 5 else ""))
    else:
        problems.append("panels/ folder missing")

    return {
        "item": item_dir.name,
        "entries": entry_count,
        "uncovered_panels": uncovered,
        "problems": problems,
        "ok": not problems,
    }


def main() -> int:
    from mangaeasy.video_pipeline.common import DEFAULT_PROJECT_ROOT, item_dirs, merge_item_selection

    parser = argparse.ArgumentParser(
        prog="mangaeasy narration-check",
        description="Validate narration.json/intro.json structure per item: "
                    "parseable, every entry's image exists, every panel is "
                    "covered, no empty narration. Semantic review (accuracy, "
                    "speaker attribution) remains an agent's reading job.",
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT,
                        help="Project folder containing item subfolders (library/<name>).")
    parser.add_argument("--items", nargs="*", help="Item folders, e.g. 01 02 05-08 (default: all).")
    parser.add_argument("--item-range", help="Inclusive item range, e.g. 01-19.")
    parser.add_argument("--json", action="store_true", help="Emit one JSON object on stdout.")
    args = parser.parse_args()

    selection = merge_item_selection(args.items, args.item_range)
    selected = item_dirs(Path(args.project_root), selection)
    if not selected:
        message = f"no items found under {args.project_root}"
        if args.json:
            print(json.dumps({"ok": False, "error": message, "items": []}))
        else:
            print(f"[ERROR] {message}")
        return 1

    reports = [check_item(item_dir) for item_dir in selected]
    ok = all(r["ok"] for r in reports)

    if args.json:
        print(json.dumps({"ok": ok, "items": reports}, ensure_ascii=False))
        return 0 if ok else 1

    print(f"narration-check: {args.project_root} ({len(reports)} item(s))\n")
    for r in reports:
        status = "ok " if r["ok"] else "FAIL"
        print(f"  [{status}] {r['item']}: {r['entries']} entries")
        for problem in r["problems"]:
            print(f"         - {problem}")
    print("\nAll clean." if ok else "\nFix the problems above, then re-run.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
