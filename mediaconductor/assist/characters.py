"""mediaconductor.assist.characters — the per-project character registry.

``<project-root>/characters.json`` records who appears in a series: canonical
name, aliases, appearance, role. Narration quality lives or dies on speaker
attribution, and in production small agents kept inventing names or swapping
speakers mid-series. The registry gives every later stage (``narrate-auto``,
prompts an agent writes by hand, review passes) one authoritative cast list.

File shape:

    {
      "draft": true,                      // set false after human review
      "characters": [
        {"name": "Ren",
         "aliases": ["the swordsman"],
         "appearance": "silver hair, red scarf, dual swords",
         "role": "protagonist",
         "notes": "younger brother of Mina"}
      ]
    }

``--auto-draft`` samples panels across the selected items and asks the local
Gemma 4 model (vision) to propose the recurring cast, cross-checked against
OCR names when a transcript exists. The result is ALWAYS written with
``"draft": true`` — review the names against the actual story before relying
on them (OCR romanization and model guesses both need a human eye).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mediaconductor.brand import CLI_NAME
from mediaconductor.utils import emit_result

PANEL_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

_TEMPLATE = {
    "draft": True,
    "characters": [
        {
            "name": "ExampleName",
            "aliases": [],
            "appearance": "hair/build/clothing cues that identify them in panels",
            "role": "protagonist | antagonist | support",
            "notes": "",
        }
    ],
}

_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "appearance": {"type": "string"},
                    "role": {"type": "string"},
                },
                "required": ["name", "appearance"],
            },
        }
    },
    "required": ["characters"],
}


def characters_path(project_root: Path) -> Path:
    return project_root / "characters.json"


def load_characters(project_root: Path) -> dict | None:
    """Parsed registry, or None when absent/unreadable (callers degrade)."""
    path = characters_path(project_root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("characters"), list) else None


def validate_registry(data: dict) -> list[str]:
    problems: list[str] = []
    names: set[str] = set()
    for index, entry in enumerate(data.get("characters", [])):
        if not isinstance(entry, dict) or not str(entry.get("name", "")).strip():
            problems.append(f"characters[{index}]: missing name")
            continue
        name = str(entry["name"]).strip().lower()
        if name in names:
            problems.append(f"characters[{index}]: duplicate name '{entry['name']}'")
        names.add(name)
        if not str(entry.get("appearance", "")).strip():
            problems.append(f"characters[{index}] ({entry['name']}): missing appearance")
    return problems


def registry_prompt_block(data: dict | None) -> str:
    """The cast list as a prompt block, or a no-registry instruction."""
    if not data or not data.get("characters"):
        return ("No character registry exists for this project. Refer to characters "
                "only by what is visible or by names explicitly present in the OCR "
                "text; NEVER invent names.")
    lines = ["Known characters (use EXACTLY these names; do not invent others):"]
    for entry in data["characters"]:
        aliases = ", ".join(entry.get("aliases") or [])
        parts = [f"- {entry.get('name')}"]
        if aliases:
            parts.append(f"(aka {aliases})")
        if entry.get("appearance"):
            parts.append(f"— {entry['appearance']}")
        if entry.get("role"):
            parts.append(f"[{entry['role']}]")
        lines.append(" ".join(parts))
    if data.get("draft"):
        lines.append("(registry is an unreviewed draft — prefer OCR-attested names on conflict)")
    return "\n".join(lines)


def _sample_panels(item_dirs_list: list[Path], limit: int) -> list[Path]:
    panels: list[Path] = []
    for item_dir in item_dirs_list:
        panels_dir = item_dir / "panels"
        if panels_dir.is_dir():
            panels.extend(sorted(
                p for p in panels_dir.iterdir() if p.suffix.lower() in PANEL_EXTENSIONS
            ))
    if len(panels) <= limit:
        return panels
    step = len(panels) / limit
    return [panels[int(i * step)] for i in range(limit)]


def _ocr_lines(item_dirs_list: list[Path], limit: int = 60) -> list[str]:
    lines: list[str] = []
    for item_dir in item_dirs_list:
        transcript = item_dir / "transcript.json"
        if not transcript.is_file():
            continue
        try:
            entries = json.loads(transcript.read_text(encoding="utf-8-sig"))
        except ValueError:
            continue
        for entry in entries:
            text = str(entry.get("ocr") or "").strip() if isinstance(entry, dict) else ""
            if text:
                lines.append(text)
            if len(lines) >= limit:
                return lines
    return lines


def auto_draft(project_root: Path, item_dirs_list: list[Path], work_dir: Path,
               *, sample: int = 24, chunk: int = 6) -> dict:
    """Draft the registry with Gemma vision; merged across panel chunks."""
    from mediaconductor.tools.gemma import batch_generate, parse_json_reply

    panels = _sample_panels(item_dirs_list, sample)
    if not panels:
        raise RuntimeError("no panels found — run the splitter before --auto-draft")
    ocr = _ocr_lines(item_dirs_list)
    ocr_block = ("Dialogue text seen in this series (OCR, may contain names):\n"
                 + "\n".join(f"- {line}" for line in ocr[:40])) if ocr else \
                "No OCR transcript exists; only name characters you can read on-panel."
    system = (
        "You catalog the recurring cast of a manga/webtoon from sample panels. "
        "Only include characters that clearly recur or matter to the story. "
        "Use a name ONLY when the dialogue text or on-panel text states it; "
        "otherwise use a short descriptive handle like 'silver-haired swordsman'. "
        "Appearance must list stable visual cues (hair, clothing, build) usable to "
        "recognize them in other panels. Respond with JSON only."
    )
    requests = []
    for start in range(0, len(panels), chunk):
        group = panels[start:start + chunk]
        prompt = (f"{ocr_block}\n\nThese are sample panels "
                  f"{start + 1}-{start + len(group)} of the series. List the "
                  "recurring characters you can identify in them.")
        requests.append({
            "prompt": prompt,
            "system": system,
            "images": [str(p) for p in group],
            "json_schema": _DRAFT_SCHEMA,
        })
    replies = batch_generate(requests, work_dir=work_dir / "characters_draft",
                             max_tokens=700, temperature=0.2)

    merged: dict[str, dict] = {}
    for reply in replies:
        data = parse_json_reply(reply)
        if not isinstance(data, dict):
            continue
        for entry in data.get("characters") or []:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            if not name:
                continue
            key = name.lower()
            if key in merged:
                merged[key]["sightings"] += 1
            else:
                merged[key] = {
                    "name": name,
                    "aliases": [],
                    "appearance": str(entry.get("appearance", "")).strip(),
                    "role": str(entry.get("role", "")).strip(),
                    "notes": "",
                    "sightings": 1,
                }
    # Characters seen in one chunk only are usually one-scene extras; keep them
    # but sort recurring ones first so review starts with the real cast.
    cast = sorted(merged.values(), key=lambda c: -c["sightings"])
    return {"draft": True, "characters": cast}


def main() -> int:
    from mediaconductor.video_pipeline.common import (
        DEFAULT_PROJECT_ROOT, DEFAULT_WORK_DIR, item_dirs, merge_item_selection,
    )

    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} characters",
        description="Create/validate the per-project character registry "
                    "(characters.json) that grounds narration and speaker attribution.",
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--items", nargs="*", help="Items to sample for --auto-draft.")
    parser.add_argument("--item-range")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--init", action="store_true",
                        help="Write a template characters.json to fill in by hand.")
    parser.add_argument("--auto-draft", action="store_true",
                        help="Draft the registry with the local Gemma 4 model "
                             f"(install first: {CLI_NAME} install-tool gemma-4). "
                             "Existing registries are not overwritten without --overwrite.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--sample-panels", type=int, default=24)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    path = characters_path(project_root)

    if args.init or args.auto_draft:
        if path.exists() and not args.overwrite:
            print(f"[error] {path} already exists — pass --overwrite to replace it "
                  "(existing registries usually contain reviewed names).", flush=True)
            return 1
        if args.init:
            registry = _TEMPLATE
        else:
            try:
                selected = item_dirs(project_root, merge_item_selection(args.items, args.item_range))
                registry = auto_draft(project_root, selected, args.work_dir.resolve(),
                                      sample=args.sample_panels)
            except RuntimeError as exc:
                print(f"[error] {exc}", flush=True)
                return 1
        path.write_text(json.dumps(registry, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
        print(f"Wrote {path} ({len(registry['characters'])} character(s), draft=true).")
        print("Review every name/appearance against the actual story, fix them, then "
              "set \"draft\": false.")
        emit_result(command="characters", file=path,
                    characters=len(registry["characters"]), draft=True)
        return 3  # artifact created, review required

    registry = load_characters(project_root)
    if registry is None:
        message = (f"no characters.json at {path} — create one with --init or "
                   f"--auto-draft")
        if args.as_json:
            print(json.dumps({"file": str(path), "exists": False, "note": message}))
        else:
            print(message)
        return 1
    problems = validate_registry(registry)
    if args.as_json:
        print(json.dumps({"file": str(path), "exists": True,
                          "draft": bool(registry.get("draft")),
                          "characters": registry.get("characters", []),
                          "problems": problems}, ensure_ascii=False))
    else:
        print(registry_prompt_block(registry))
        for problem in problems:
            print(f"[problem] {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
