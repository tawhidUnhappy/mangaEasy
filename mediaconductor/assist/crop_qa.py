"""mediaconductor.assist.crop_qa — LLM-vision review of crop verification artifacts.

The splitters flag exactly where crops may be wrong (forced cuts, short
panels, suspect pages), but acting on those flags used to require a
multimodal driver agent that actually opened the sheets — the step small or
text-only agents skipped, shipping sliced speech bubbles into narration. This
command performs that review with the local Gemma 4 model and prints the
exact override command for every location it judges broken.

Webtoon items: renders a full-resolution window around every forced auto-split
cut and short panel recorded in the ranges manifest (same geometry as
``webtoon-cutcheck``) and asks per window: does the marked cut slice through a
figure or speech bubble / is the short panel a fragment of its neighbour?

Paged items: reviews every page overlay (numbered MAGI boxes) for missed,
merged, or misordered panels, incomplete crops that cut off panel art, and
boxes inflated by gutter whitespace that would render as an unreadably
narrow sliver once fit to the 16:9 video frame.

Verdicts land in ``<work-dir>/crop_qa/<project>/<item>_report.json``. Exit 3
means fixes are proposed — apply them (``webtoon-override`` + re-split, or a
page-split ``--overrides`` file), re-split, and re-run until exit 0. The
model is a reviewer, not an oracle: spot-check its FIX verdicts on the
referenced window images, especially before large re-crops.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mediaconductor.brand import CLI_NAME
from mediaconductor.utils import emit_result

_WEBTOON_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["fix", "accept"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
}

_PAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["fix", "ok"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
}

_WEBTOON_SYSTEM = (
    "You are a manga webtoon crop reviewer. The image is a window of a vertical "
    "comic strip. Horizontal colored lines mark panel boundaries chosen by an "
    "automatic splitter (a RED line is a cut, GREEN/ORANGE lines are the top/"
    "bottom of a short panel). Verdict 'fix' when a marked boundary slices "
    "through a character's figure or face, a speech bubble, or caption text, or "
    "when a short panel is clearly a fragment whose art continues past its "
    "boundary. Verdict 'accept' when boundaries pass through background/effect "
    "art or real gutters, and for scanlator credit/promo banners (those are "
    "skipped in narration instead). Respond with JSON only."
)

_PAGE_SYSTEM = (
    "You are a manga page-segmentation reviewer. The image is a manga page with "
    "numbered red boxes marking detected panels in reading order. Verdict 'fix' "
    "when panels are missing a box, one box merges multiple distinct panels, a "
    "box cuts through a panel (does not fully contain it — a missing edge or "
    "corner of the art), or the numbering order clearly contradicts the "
    "reading order. Also verdict 'fix' when a box is far taller than it is "
    "wide because it swallowed blank gutter above or below the panel instead "
    "of hugging the art — say so and note it should be tightened to the "
    "panel's actual content: the rendered video frame is 16:9 landscape, so a "
    "needlessly tall crop shrinks to an unreadable sliver once fit to it (a "
    "squarish, 1:1-ish crop is fine; only flag real excess gutter, not a "
    "panel that is genuinely that tall, like a full-body action shot). "
    "Verdict 'ok' otherwise; a single box covering a full splash page, "
    "chapter title, or credits page is normal and 'ok'. "
    "Respond with JSON only."
)


def review_webtoon_item(item_dir: Path, verify_dir: Path, out_dir: Path,
                        args) -> dict | None:
    """Render review windows for one webtoon item; returns request plan."""
    from mediaconductor.panels.cutcheck import (
        GREEN, ORANGE, RED, parse_forced_cuts, render_window, stitch_pages, window_bounds,
    )

    item = item_dir.name
    manifest_path = verify_dir / f"{item}_ranges.json"
    if not manifest_path.is_file():
        print(f"[{item}] no ranges manifest at {manifest_path} — run webtoon-split first")
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    strip = stitch_pages(item_dir / args.source_subdir)
    windows: list[dict] = []
    for y in parse_forced_cuts(manifest):
        top, bottom = window_bounds(y, y, strip.height, args.window)
        image = out_dir / f"{item}_cut_y{y}.jpg"
        render_window(strip, top, bottom, args.thumb_width,
                      [(y, RED, f"CUT y={y}")]).save(image, quality=88)
        windows.append({
            "kind": "cut", "y": y, "image": str(image),
            "fix_command": (f"{CLI_NAME} webtoon-override --file {out_dir / 'overrides.json'} "
                            f"--project-root {item_dir.parent} --item {item} "
                            f"--merge-at-cut {y}"),
        })
    for panel in manifest.get("final", []):
        if panel.get("height", 0) >= args.short_height:
            continue
        top, bottom = window_bounds(panel["top"], panel["bottom"], strip.height, args.window)
        image = out_dir / f"{item}_short_p{panel['index']:03d}.jpg"
        render_window(strip, top, bottom, args.thumb_width, [
            (panel["top"], GREEN, f"#{panel['index']} top"),
            (panel["bottom"], ORANGE, f"#{panel['index']} bottom"),
        ]).save(image, quality=88)
        windows.append({
            "kind": "short_panel", "panel": panel["index"], "image": str(image),
            "fix_command": (f"{CLI_NAME} webtoon-override --file {out_dir / 'overrides.json'} "
                            f"--project-root {item_dir.parent} --item {item} "
                            f"--merge-panels <neighbour>,{panel['index']}  "
                            f"# fuse with whichever neighbour the art continues into"),
        })
    return {"item": item, "windows": windows,
            "resplit_command": (f"{CLI_NAME} webtoon-split --project-root {item_dir.parent} "
                                f"--items {item} --overrides {out_dir / 'overrides.json'}")}


def review_paged_item(item_dir: Path, verify_dir: Path) -> dict | None:
    item = item_dir.name
    item_verify = verify_dir / item
    overlays = sorted(item_verify.glob(f"{item}_page_*.png")) if item_verify.is_dir() else []
    if not overlays:
        print(f"[{item}] no page overlays under {item_verify} — run page-split first")
        return None
    windows = [{
        "kind": "page", "page": overlay.stem.rsplit("_", 1)[-1], "image": str(overlay),
        "fix_command": (f"add {overlay.name.replace('_page_', ' page ')}'s corrected "
                        f"[[x1,y1,x2,y2], ...] boxes to a page-split --overrides file "
                        f"(raw detections: {item_verify / f'{item}_detections.json'})"),
    } for overlay in overlays]
    return {"item": item, "windows": windows,
            "resplit_command": (f"{CLI_NAME} page-split --project-root {item_dir.parent} "
                                f"--items {item} --overrides <overrides.json>")}


def detect_style(item_dir: Path, webtoon_verify: Path, page_verify: Path) -> str | None:
    if (webtoon_verify / f"{item_dir.name}_ranges.json").is_file():
        return "webtoon"
    if (page_verify / item_dir.name).is_dir():
        return "paged"
    return None


def main() -> int:
    from mediaconductor.tools.gemma import (
        GemmaUnavailable, batch_generate, parse_json_reply, resolve_gemma,
    )
    from mediaconductor.video_pipeline.common import (
        DEFAULT_PROJECT_ROOT, DEFAULT_WORK_DIR, item_dirs, merge_item_selection,
    )

    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} crop-qa",
        description="Review crop verification artifacts with the local Gemma 4 model; "
                    "flags bad cuts/boxes and prints the override commands to fix them.",
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--items", nargs="*")
    parser.add_argument("--item-range")
    parser.add_argument("--style", choices=("auto", "webtoon", "paged"), default="auto")
    parser.add_argument("--source-subdir", default="download")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--window", type=int, default=650)
    parser.add_argument("--short-height", type=int, default=460)
    parser.add_argument("--thumb-width", type=int, default=650)
    parser.add_argument("--json", action="store_true", dest="as_json")
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
    webtoon_verify = (args.work_dir / "webtoon_verify" / project_root.name).resolve()
    page_verify = (args.work_dir / "page_verify" / project_root.name).resolve()
    out_root = (args.work_dir / "crop_qa" / project_root.name).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    plans: list[dict] = []
    for item_dir in selected:
        style = args.style if args.style != "auto" else detect_style(
            item_dir, webtoon_verify, page_verify)
        if style is None:
            print(f"[{item_dir.name}] no split artifacts found — run a splitter first")
            continue
        plan = (review_webtoon_item(item_dir, webtoon_verify, out_root, args)
                if style == "webtoon" else review_paged_item(item_dir, page_verify))
        if plan is not None:
            plan["style"] = style
            plans.append(plan)
    requests = []
    for plan in plans:
        for window in plan["windows"]:
            if plan["style"] == "webtoon":
                prompt = ("Judge the marked boundary in this webtoon strip window. "
                          f"(flag kind: {window['kind']})")
                system, schema = _WEBTOON_SYSTEM, _WEBTOON_SCHEMA
            else:
                prompt = "Judge the numbered panel boxes on this manga page."
                system, schema = _PAGE_SYSTEM, _PAGE_SCHEMA
            requests.append({"prompt": prompt, "system": system,
                             "images": [window["image"]], "json_schema": schema})
    if not requests:
        print("crop-qa: nothing to review (no flagged locations / overlays).")
        emit_result(command="crop-qa", items={}, fixes=0)
        return 0

    replies = batch_generate(requests, work_dir=out_root / "_llm",
                             max_tokens=250, temperature=0.1)

    reply_iter = iter(replies)
    fixes = 0
    unreadable = 0
    report_items: dict[str, dict] = {}
    for plan in plans:
        item_report = {"style": plan["style"], "resplit_command": plan["resplit_command"],
                       "windows": []}
        for window in plan["windows"]:
            parsed = parse_json_reply(next(reply_iter))
            verdict = (parsed or {}).get("verdict") if isinstance(parsed, dict) else None
            reason = (parsed or {}).get("reason", "") if isinstance(parsed, dict) else ""
            if verdict not in ("fix", "accept", "ok"):
                verdict, reason = "unreviewed", "model reply was unusable — inspect manually"
                unreadable += 1
            entry = {**window, "verdict": verdict, "reason": reason}
            if verdict == "fix":
                fixes += 1
            item_report["windows"].append(entry)
        report_path = out_root / f"{plan['item']}_report.json"
        report_path.write_text(json.dumps(item_report, ensure_ascii=False, indent=1),
                               encoding="utf-8")
        report_items[plan["item"]] = {**item_report, "report": str(report_path)}

    if args.as_json:
        print(json.dumps({"items": report_items, "fixes": fixes,
                          "unreviewed": unreadable}, ensure_ascii=False))
    else:
        for name, item_report in report_items.items():
            for window in item_report["windows"]:
                if window["verdict"] in ("accept", "ok"):
                    continue
                print(f"[{name}] {window['verdict'].upper()}: {window['reason']}")
                print(f"        window: {window['image']}")
                print(f"        fix:    {window['fix_command']}")
            if any(w["verdict"] == "fix" for w in item_report["windows"]):
                print(f"[{name}] after adding fixes: {item_report['resplit_command']}")
    emit_result(command="crop-qa", items=report_items, fixes=fixes, unreviewed=unreadable)
    if fixes or unreadable:
        print(f"crop-qa: {fixes} fix(es) proposed, {unreadable} window(s) unreviewed — "
              "apply the printed override commands, re-split, and re-run crop-qa.")
        return 3
    print("crop-qa: all flagged locations accepted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
