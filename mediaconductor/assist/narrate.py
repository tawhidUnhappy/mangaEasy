"""mediaconductor.assist.narrate — draft grounded narration with the local LLM.

``mediaconductor narrate-auto`` writes ``<item>/narration.json`` the same way
the skill instructs a strong agent to: panel by panel, grounded in the panel
image and its OCR transcript, using only registry-attested character names,
skipping credit/promo banners. It exists for driver agents that cannot (or
should not) read panel images themselves.

Grounding chain per item:

  panels/ + transcript.json + characters.json
    → chunked vision requests (each panel image + its OCR text)
    → per-panel {narration | skip} + a running story-so-far summary
    → narration.json → `narration-check` → review sheets

The command always exits 3 on success: the draft REQUIRES review against the
generated ``narration-review-sheets`` before TTS — exactly like a
human-written narration. Existing ``narration.json`` files are never
overwritten without ``--overwrite``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mediaconductor import runtime
from mediaconductor.audio.emotion import (
    SUGGESTED_EMOTIONS,
    emotion_lint,
    narration_delivery_lint,
    narration_emotion,
)
from mediaconductor.brand import CLI_NAME
from mediaconductor.runtime import cli_command
from mediaconductor.utils import emit_result

PANEL_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

_CHUNK_SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "image": {"type": "string"},
                    "narration": {"type": "string"},
                    "emotion": {"type": "string", "enum": list(SUGGESTED_EMOTIONS)},
                    "skip": {"type": "boolean"},
                },
                "required": ["image"],
            },
        },
        "story_so_far": {"type": "string"},
    },
    "required": ["entries", "story_so_far"],
}

_SYSTEM_TEMPLATE = (
    "You write recap narration for a manga/webtoon video, one entry per panel, "
    "spoken aloud by a narrator.\n"
    "Rules:\n"
    "- Describe only what is visible in the panel or established earlier; never "
    "invent dialogue, motives, off-panel events, or visual details.\n"
    "- OCR text is evidence for what bubbles say — compare it with the panel "
    "before quoting or paraphrasing; OCR can be wrong.\n"
    "- Keep names, pronouns, relationships, and speaker attribution consistent.\n"
    "- Write simple, natural spoken prose, 1-2 sentences per panel; vary sentence "
    "openings; no markdown, no quotes around the whole line.\n"
    "- The narrator is always a calm observer, even during fights, deaths, "
    "battle cries, shocks, and character outbursts. Convey events through plain "
    "words, never through a loud performance.\n"
    "- Set \"skip\": true (with empty narration) for scanlator credits, promo "
    "banners, and purely decorative/SFX fragments that carry no story.\n"
    "- Omit the optional \"emotion\" field for neutral delivery. When a subtle "
    "tone is genuinely helpful, its value MUST be exactly one of: 'calm', "
    "'neutral', 'slightly sad', or 'slightly happy'. Never use tense, urgent, "
    "fearful, panicked, angry, excited, shocked, scream, shout, or any other "
    "high-intensity delivery hint.\n"
    "- Never spell out or imitate a laugh, scream, roar, cry, or sound effect "
    "('ghaha', 'ha ha ha', 'gyahahaha', 'aaaargh'). Describe it calmly in prose: "
    "'he laughed', 'she reacted in pain', or 'the phoenix let out a cry'.\n"
    "- Do not use exclamation marks, repeated punctuation, or shout-like ALL "
    "CAPS. End narration as calm statements.\n"
    "- Return EXACTLY one entry per listed panel, in the given order, with the "
    "exact given image filename.\n\n"
    "{characters}\n"
)


def list_panels(item_dir: Path) -> list[Path]:
    panels_dir = item_dir / "panels"
    if not panels_dir.is_dir():
        return []
    return sorted(p for p in panels_dir.iterdir() if p.suffix.lower() in PANEL_EXTENSIONS)


def load_transcript(item_dir: Path) -> dict[str, str]:
    path = item_dir / "transcript.json"
    if not path.is_file():
        return {}
    try:
        entries = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return {}
    return {
        str(e["image"]): str(e.get("ocr") or "").strip()
        for e in entries if isinstance(e, dict) and e.get("image")
    }


def chunk_prompt(chunk: list[Path], ocr: dict[str, str], story_so_far: str) -> str:
    lines = []
    if story_so_far:
        lines.append(f"Story so far: {story_so_far}\n")
    lines.append(f"Narrate these {len(chunk)} panels. The attached images are in "
                 "the same order as this list:")
    for index, panel in enumerate(chunk, 1):
        text = ocr.get(panel.name, "")
        ocr_note = f' OCR: "{text}"' if text else " OCR: (no text)"
        lines.append(f"{index}. {panel.name} —{ocr_note}")
    lines.append("\nReturn JSON with one entry per panel plus an updated "
                 "story_so_far (max 120 words).")
    return "\n".join(lines)


def merge_chunk_entries(chunk: list[Path], parsed: dict | None,
                        log=print) -> tuple[list[dict], list[str], str]:
    """Validated (entries, skipped_names, story_so_far) for one chunk."""
    entries: list[dict] = []
    skipped: list[str] = []
    by_image: dict[str, dict] = {}
    story = ""
    if isinstance(parsed, dict):
        story = str(parsed.get("story_so_far") or "")
        for entry in parsed.get("entries") or []:
            if isinstance(entry, dict) and entry.get("image"):
                by_image[str(entry["image"]).strip()] = entry
    for panel in chunk:
        entry = by_image.get(panel.name)
        if entry is None:
            log(f"    [warn] model omitted {panel.name} — marked for manual narration")
            skipped.append(panel.name)
            continue
        if entry.get("skip"):
            skipped.append(panel.name)
            continue
        narration = str(entry.get("narration") or "").strip()
        if not narration:
            log(f"    [warn] empty narration for {panel.name} — marked for manual narration")
            skipped.append(panel.name)
            continue
        delivery = narration_delivery_lint(narration)
        if delivery:
            # Keep the panel in narration.json so it remains visible on review
            # sheets. The central TTS/render preflight will refuse to build
            # until a reviewer rewrites the unsafe draft.
            log(f"    [warn] {panel.name}: {delivery} - retained for manual rewrite")
        item: dict = {"image": panel.name, "narration": narration}
        raw_emotion = entry.get("emotion")
        emotion = narration_emotion(entry)
        if emotion:
            item["emotion"] = emotion
        elif raw_emotion:
            log(f"    [warn] {panel.name}: {emotion_lint(entry)} - emotion omitted")
        entries.append(item)
    return entries, skipped, story


def draft_item(item_dir: Path, work_dir: Path, characters_block: str,
               *, chunk_size: int, log=print) -> dict:
    from mediaconductor.tools.gemma import batch_generate, parse_json_reply

    item = item_dir.name
    panels = list_panels(item_dir)
    if not panels:
        return {"item": item, "status": "skipped", "reason": "no panels"}
    ocr = load_transcript(item_dir)
    system = _SYSTEM_TEMPLATE.format(characters=characters_block)

    all_entries: list[dict] = []
    all_skipped: list[str] = []
    story_so_far = ""
    chunks = [panels[i:i + chunk_size] for i in range(0, len(panels), chunk_size)]
    for index, chunk in enumerate(chunks, 1):
        log(f"  [{item}] chunk {index}/{len(chunks)} ({len(chunk)} panel(s))")
        request = {
            "prompt": chunk_prompt(chunk, ocr, story_so_far),
            "system": system,
            "images": [str(p) for p in chunk],
            "json_schema": _CHUNK_SCHEMA,
            "max_tokens": 220 * len(chunk) + 250,
        }
        # One request per call keeps the story_so_far chain strictly ordered.
        reply = batch_generate([request], work_dir=work_dir / item / f"chunk_{index:03d}",
                               ctx_size=16384, temperature=0.4, log=log)[0]
        entries, skipped, story = merge_chunk_entries(chunk, parse_json_reply(reply), log)
        all_entries.extend(entries)
        all_skipped.extend(skipped)
        story_so_far = story or story_so_far

    if not all_entries:
        return {"item": item, "status": "error", "reason": "model produced no usable entries"}
    narration_path = item_dir / "narration.json"
    narration_path.write_text(
        json.dumps(all_entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"item": item, "status": "ok", "narration": str(narration_path),
            "entries": len(all_entries), "panels": len(panels),
            "skipped": all_skipped, "story_so_far": story_so_far}


def main() -> int:
    from mediaconductor.assist.characters import load_characters, registry_prompt_block
    from mediaconductor.tools.gemma import GemmaUnavailable, resolve_gemma
    from mediaconductor.video_pipeline.common import (
        DEFAULT_PROJECT_ROOT, DEFAULT_WORK_DIR, item_dirs, merge_item_selection,
    )

    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} narrate-auto",
        description="Draft grounded narration.json with the local Gemma 4 model from "
                    "panels + OCR + the character registry. Always requires review "
                    "(exit 3) before TTS.",
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--items", nargs="*")
    parser.add_argument("--item-range")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--chunk-size", type=int, default=8,
                        help="Panels per vision request (default 8).")
    parser.add_argument("--overwrite", action="store_true",
                        help="Replace existing narration.json files (they may contain "
                             "reviewed human work — off by default).")
    parser.add_argument("--skip-checks", action="store_true",
                        help="Skip the narration-check / review-sheet follow-up runs.")
    args = parser.parse_args()

    try:
        resolve_gemma()
    except GemmaUnavailable as exc:
        print(f"[error] {exc}", flush=True)
        return 1

    project_root = args.project_root.resolve()
    selected = item_dirs(project_root, merge_item_selection(args.items, args.item_range))
    if not selected:
        print(f"[FATAL] No item folders found under {project_root}")
        return 1

    registry = load_characters(project_root)
    if registry is None:
        print(f"[note] no characters.json — narration will avoid names it cannot see. "
              f"Consider `{CLI_NAME} characters --auto-draft` first.", flush=True)
    characters_block = registry_prompt_block(registry)

    scratch = (args.work_dir / "narrate_auto" / project_root.name).resolve()
    reports: list[dict] = []
    drafted: list[str] = []
    for index, item_dir in enumerate(selected, 1):
        print(f"MEDIACONDUCTOR_PROGRESS {index}/{len(selected)} {item_dir.name}", flush=True)
        if (item_dir / "narration.json").is_file() and not args.overwrite:
            print(f"  [{item_dir.name}] narration.json exists — skipped (use --overwrite)")
            reports.append({"item": item_dir.name, "status": "skipped",
                            "reason": "narration.json exists"})
            continue
        report = draft_item(item_dir, scratch, characters_block,
                            chunk_size=args.chunk_size)
        reports.append(report)
        if report["status"] == "ok":
            drafted.append(item_dir.name)
            print(f"  [{item_dir.name}] {report['entries']} entrie(s), "
                  f"{len(report['skipped'])} skipped panel(s)")

    failed = [r["item"] for r in reports if r["status"] == "error"]
    emit_result(command="narrate-auto", items=reports)
    if not drafted:
        print("narrate-auto: nothing drafted." + (" Failures above." if failed else ""))
        return 1 if failed else 0

    if not args.skip_checks:
        base = ["--project-root", str(project_root), "--items", *drafted]
        check = runtime.run(cli_command("narration-check", *base, "--json"))
        sheets = runtime.run(cli_command(
            "narration-review-sheets", *base, "--work-dir", str(args.work_dir.resolve())))
        if check.returncode != 0 or sheets.returncode != 0:
            print("narrate-auto: draft written but narration-check/review-sheets "
                  "reported problems above — fix them before TTS.")
            return 1

    print("narrate-auto: drafts written. REVIEW REQUIRED — read every narration "
          "review sheet, fix wrong speakers/claims with narration-edit, then build.")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
