"""mangaeasy.qa_loop — the fix-until-clean loop and the reuse inventory.

``mangaeasy work-qa`` aggregates every *machine-checkable* quality gate for
the generated artifacts (crops present, OCR coverage, narration structure,
speakability, emotion fields, audio coverage + integrity, render freshness)
into one ordered problem list — each problem carrying a concrete ``fix``
command. That shape exists for small LLMs: the whole correction workflow
collapses to a loop a modest model can drive without global judgment —

    while mangaeasy work-qa ... --json reports problems:
        run the first problem's `fix`
        (re-narrate / narration-edit when the fix says so)

Exit codes make the loop trivial: 0 = clean, 1 = problems remain. Checks
that need *eyes* (crop verify sheets, narration review sheets) are surfaced
as ``review`` items pointing at the exact sheet files to read — they never
block the loop, because a vision pass, not a retry, resolves them.

``mangaeasy work-artifacts`` is the reuse inventory: everything expensive
this project has already generated (per-item videos, long-video takes,
archived audio runs, cached music beds, transcripts, QA sheets), each with
a hint for how to reuse it instead of regenerating.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mangaeasy.audio.emotion import emotion_lint
from mangaeasy.video_pipeline.check_items import is_speakable
from mangaeasy.video_pipeline.common import (
    DEFAULT_AUDIO_ROOT,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PROJECT_ROOT,
    DEFAULT_WORK_DIR,
    item_dirs,
    merge_item_selection,
    project_name,
)
from mangaeasy.video_pipeline.item_assets import load_narration
from mangaeasy.video_pipeline.narration_check import check_item
from mangaeasy.workboard import item_status

# Below this size a WAV cannot hold audible narration — it is a truncated or
# failed TTS write (the audible-audio deep check lives in video-audio-audit).
MIN_AUDIO_BYTES = 1024


def _stage_fix(stage: str, project_root: str, item: str) -> str:
    return {
        "download": f"mangaeasy download --url <mangadex url> --name {Path(project_root).name} --chapters {item}",
        "crop": f"mangaeasy page-split --project-root {project_root} --items {item}   (webtoon-split for vertical strips; then READ the verify sheets)",
        "transcribe": f"mangaeasy panel-transcript --project-root {project_root} --items {item}",
        "narrate": f"write {project_root}/{item}/narration.json grounded in {item}/transcript.json (see mangaeasy/assets/prompts/narration.md), then re-run work-qa",
        "audio": f"mangaeasy video --project-root {project_root} --items {item} --tts auto",
        "render": f"mangaeasy video --project-root {project_root} --items {item} --skip-audio --overwrite-video",
    }[stage]


def qa_item(item_dir: Path, name: str, project_root: Path,
            audio_root: Path, output_root: Path, work_dir: Path) -> list[dict]:
    """Ordered problems for one item; empty list means machine-clean."""
    problems: list[dict] = []
    root_arg = str(project_root)
    item = item_dir.name

    def add(severity: str, kind: str, detail: str, fix: str) -> None:
        problems.append({"item": item, "severity": severity, "kind": kind,
                         "detail": detail, "fix": fix})

    status = item_status(item_dir, name, audio_root, output_root)

    # 1. Pipeline completeness — the loop's backbone: whatever stage is
    #    missing next is the first fix, in production order.
    stage = status["next_stage"]
    if stage in ("download", "crop", "transcribe"):
        detail = {
            "download": "no source pages downloaded",
            "crop": "no panels cropped yet",
            "transcribe": f"OCR transcript incomplete ({status['transcript']['filled']}/{status['transcript']['total']})",
        }[stage]
        add("error", f"stage:{stage}", detail, _stage_fix(stage, root_arg, item))
        return problems  # later checks are meaningless before these exist

    if stage == "narrate":
        add("error", "stage:narrate", "no narration.json (or zero entries)",
            _stage_fix("narrate", root_arg, item))
        return problems

    # 2. Narration structure (dangling images, empty text, intro overlap...).
    report = check_item(item_dir)
    for problem in report["problems"]:
        if "no narration entry" in problem:
            continue  # uncovered panels are reported as review info below
        add("error", "narration:structure", problem,
            f"mangaeasy narration-edit --project-root {root_arg} --item {item} --list  "
            f"(then fix the entry with --set/--delete --prune-audio)")
    if report["uncovered_panels"]:
        add("info", "narration:uncovered",
            f"{len(report['uncovered_panels'])} panel(s) have no narration entry "
            "(correct for credits/banners/SFX; confirm none is a story panel)",
            f"mangaeasy narration-review-sheets --project-root {root_arg} --items {item} and READ the sheets")

    # 3. Speakability + emotion lint, per entry.
    try:
        entries = load_narration(item_dir)
    except Exception:  # noqa: BLE001 — structure errors already reported above
        entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        image = entry.get("image", "?")
        text = (entry.get("narration") or entry.get("text") or "").strip()
        if text and not is_speakable(text):
            add("error", "narration:unspeakable",
                f"{image}: narration has no letters/digits (TTS emits near-silence): {text!r}",
                f"mangaeasy narration-edit --project-root {root_arg} --item {item} "
                f"--set {image} \"<speakable line>\" --prune-audio")
        lint = emotion_lint(entry)
        if lint:
            add("error", "narration:emotion", f"{image}: {lint}",
                f"mangaeasy narration-edit --project-root {root_arg} --item {item} "
                f"--set-json '[{{\"image\": \"{image}\", ...}}]'  (fix or drop the emotion field)")

    # 4. Audio coverage + integrity (cheap size gate; deep decode check is
    #    video-audio-audit).
    audio_dir = audio_root / name / item
    missing, corrupt = [], []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("image"):
            continue
        text = (entry.get("narration") or entry.get("text") or "").strip()
        if not text:
            continue
        wav = audio_dir / f"{Path(entry['image']).stem}.wav"
        if not wav.is_file():
            missing.append(wav.name)
        elif wav.stat().st_size < MIN_AUDIO_BYTES:
            corrupt.append(wav.name)
    if missing:
        add("error", "audio:missing", f"{len(missing)} narration line(s) have no WAV: {', '.join(missing[:5])}…",
            _stage_fix("audio", root_arg, item))
    if corrupt:
        add("error", "audio:corrupt", f"{len(corrupt)} WAV(s) too small to be real audio: {', '.join(corrupt[:5])}",
            f"mangaeasy video-audio-audit --project-root {root_arg} --items {item} --fix, then "
            + _stage_fix("audio", root_arg, item))

    # 5. Render existence/freshness.
    if stage == "render":
        detail = "item video is stale (narration changed after render)" if status["render_stale"] \
            else "item video not rendered yet"
        add("error", "render:" + ("stale" if status["render_stale"] else "missing"), detail,
            _stage_fix("render", root_arg, item))

    # 6. Vision-required review artifacts, if present: point at them, never block.
    verify_dir = work_dir / "page_verify" / name / item
    if not verify_dir.is_dir():
        verify_dir = work_dir / "webtoon_verify" / name / item
    if verify_dir.is_dir():
        sheets = sorted(str(p) for p in verify_dir.glob("*sheet*"))
        if sheets:
            add("review", "crop:verify-sheets",
                f"{len(sheets)} crop verify sheet(s) to READ for cut-through-figure/bubble errors",
                f"Read {sheets[0]} … then fix bad cuts (webtoon-override / page-split --overrides) and re-split")
    return problems


def qa_main() -> int:
    parser = argparse.ArgumentParser(
        prog="mangaeasy work-qa",
        description="One aggregated pass/fail QA gate over generated crops, narration, audio and "
                    "renders — every problem comes with the exact fix command, so a small model can "
                    "loop `work-qa → apply first fix → work-qa` until exit code 0.",
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--items", nargs="*", help="Item folders, e.g. 01 02 05-08 (default: all).")
    parser.add_argument("--item-range", help="Inclusive item range, e.g. 01-22.")
    parser.add_argument("--max-problems", type=int, default=25,
                        help="Cap the list so it fits a small context window (default 25; 0 = all).")
    parser.add_argument("--errors-only", action="store_true",
                        help="Hide review/info items — only what blocks the build.")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    root = args.project_root
    if not root.is_dir():
        print(f"[ERROR] project root not found: {root}", file=sys.stderr)
        return 1
    name = project_name(root, args.project_name)
    selection = merge_item_selection(args.items, args.item_range)

    problems: list[dict] = []
    for item_dir in item_dirs(root, selection):
        problems.extend(qa_item(item_dir, name, root, args.audio_root, args.output_root, args.work_dir))

    if args.errors_only:
        problems = [p for p in problems if p["severity"] == "error"]
    errors = sum(1 for p in problems if p["severity"] == "error")
    total = len(problems)
    if args.max_problems:
        problems = problems[: args.max_problems]

    if args.as_json:
        print(json.dumps({"ok": errors == 0, "errors": errors, "total_problems": total,
                          "shown": len(problems), "problems": problems}, ensure_ascii=False))
    else:
        if not problems:
            print("CLEAN — no machine-checkable problems.")
        for p in problems:
            print(f"[{p['severity'].upper()}] {p['item']} {p['kind']}: {p['detail']}")
            print(f"    fix: {p['fix']}")
        if total > len(problems):
            print(f"(+{total - len(problems)} more — fix these first, then re-run)")
    return 0 if errors == 0 else 1


# ── work-artifacts: what already exists and how to reuse it ─────────────────

def _dir_entry(path: Path, reuse: str, pattern: str = "*") -> dict | None:
    if not path.is_dir():
        return None
    files = [p for p in path.rglob(pattern) if p.is_file()]
    if not files:
        return None
    return {"path": str(path), "files": len(files),
            "bytes": sum(p.stat().st_size for p in files), "reuse": reuse}


def artifacts_main() -> int:
    parser = argparse.ArgumentParser(
        prog="mangaeasy work-artifacts",
        description="Inventory of every reusable generated artifact for a project — check here "
                    "before regenerating anything expensive.",
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    root = args.project_root
    if not root.is_dir():
        print(f"[ERROR] project root not found: {root}", file=sys.stderr)
        return 1
    name = project_name(root, args.project_name)

    categories = {
        "item_videos": _dir_entry(
            args.output_root / name / "items",
            "final per-item renders — video-join reuses them as-is; `video --skip-audio` re-renders only stale ones",
            "*.mp4"),
        "long_videos": _dir_entry(
            args.output_root / name,
            "joined long videos (timestamped, never clobbered) — video-add-bgm/video-normalize-audio "
            "rework these without re-joining", "*_full_*.mp4"),
        "output_archive": _dir_entry(
            args.output_root / name / "old",
            "archived earlier long-video takes (old/run_NNNN) — restorable by copying back"),
        "narration_audio": _dir_entry(
            args.audio_root / name,
            "generated TTS WAVs — any pipeline rerun without --overwrite-audio reuses them", "*.wav"),
        "audio_takes": _dir_entry(
            args.audio_root / name / "old",
            "archived audio takes — list with audio-takes-list, bring back with audio-takes-restore"),
        "transcripts": {
            "path": str(root), "files": sum(1 for d in item_dirs(root) if (d / "transcript.json").is_file()),
            "bytes": sum((d / "transcript.json").stat().st_size for d in item_dirs(root)
                         if (d / "transcript.json").is_file()),
            "reuse": "OCR ground truth — re-narrate any chapter without re-running panel-transcript",
        },
        "crop_verify_sheets": (_dir_entry(args.work_dir / "page_verify" / name,
                                          "crop QA sheets — re-READ after any re-split")
                               or _dir_entry(args.work_dir / "webtoon_verify" / name,
                                             "crop QA sheets — re-READ after any re-split")),
        "narration_review_sheets": _dir_entry(
            args.work_dir / "narration_review" / name,
            "panel+narration+OCR sheets — re-READ after narration edits"),
        "music_beds": _dir_entry(
            args.work_dir / "music_bed",
            "conditioned/looped BGM beds cached by content hash — video-add-bgm reuses them automatically",
            "*.flac"),
        "workboard": _dir_entry(
            root / ".workboard",
            "multi-agent claims + shared notes — see work-status / work-note"),
    }
    categories = {k: v for k, v in categories.items() if v and v["files"]}

    if args.as_json:
        print(json.dumps({"project": name, "artifacts": categories}, ensure_ascii=False))
        return 0
    if not categories:
        print("No generated artifacts yet.")
        return 0
    print(f"Reusable artifacts for {name}:")
    for key, info in categories.items():
        size_mb = info["bytes"] / 1_000_000
        print(f"  {key}: {info['files']} file(s), {size_mb:.1f} MB — {info['path']}")
        print(f"    reuse: {info['reuse']}")
    return 0
