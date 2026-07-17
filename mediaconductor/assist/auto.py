"""mediaconductor.assist.auto — the one-command manga pipeline for small agents.

``mediaconductor manga-auto`` encodes the decision tree the skill docs teach a
strong agent, so a driver that can only run commands still takes the correct
path:

  prep (default stage):
    download (--url) → style-detect → the CORRECT splitter (webtoon/page)
    → crop-qa (Gemma vision, when installed) → panel-transcript (when
    DeepSeek-OCR 2 is installed) → narrate-auto (Gemma) → review gate (exit 3)

  build (run after reviewing the sheets):
    video (TTS + render + join + normalize) → video-validate → work-qa

Every stage is the ordinary CLI command in a subprocess — identical behavior
to running them by hand, same logs, same artifacts, resumable at any point by
re-running. The command NEVER publishes; YouTube upload remains an explicit,
separate act. Exit 3 always means "artifacts are ready, review them" — the
printed checklist says exactly what to look at and what to run next.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from mediaconductor import runtime
from mediaconductor.brand import CLI_NAME
from mediaconductor.runtime import cli_command
from mediaconductor.utils import emit_result, parse_result_marker


def run_stage(title: str, argv: list[str]) -> tuple[int, dict | None]:
    """Stream one CLI stage; returns (exit code, last result marker payload)."""
    print(f"\n=== {title} ===\n$ {' '.join(argv)}", flush=True)
    proc = runtime.popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", bufsize=1)
    assert proc.stdout is not None
    result: dict | None = None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        parsed = parse_result_marker(line)
        if parsed is not None:
            result = parsed
    return proc.wait(), result


def _item_args(args) -> list[str]:
    argv: list[str] = []
    if args.items:
        argv += ["--items", *args.items]
    if args.item_range:
        argv += ["--item-range", args.item_range]
    return argv


def resolve_project_root(args, download_result: dict | None) -> Path | None:
    if args.project_root is not None:
        return args.project_root.resolve()
    if download_result and download_result.get("project"):
        return Path(str(download_result["project"])).resolve()
    if args.name:
        from mediaconductor.paths import manga_dir

        return manga_dir(args.name).resolve()
    return None


def prep(args) -> int:
    from mediaconductor.tools.external import resolve_tool_dir
    from mediaconductor.tools.gemma import gemma_ready

    download_result: dict | None = None
    if args.url:
        argv = cli_command("download", "--url", args.url)
        if args.name:
            argv += ["--name", args.name]
        argv += ["--chapters", *args.download_chapters] if args.download_chapters else ["--all"]
        code, download_result = run_stage("download", argv)
        if code != 0:
            print("manga-auto: download failed/incomplete — re-run to resume.")
            return code

    project_root = resolve_project_root(args, download_result)
    if project_root is None or not project_root.is_dir():
        print("manga-auto: could not resolve the project folder — pass --project-root "
              "(library/<name>) or --url/--name.")
        return 2
    print(f"\nmanga-auto: project {project_root}")

    print("\n=== style-detect ===", flush=True)
    proc = runtime.run(cli_command("style-detect", "--project-root", str(project_root),
                                   *_item_args(args), "--json"),
                       capture_output=True, text=True)
    sys.stdout.write(proc.stdout or "")
    verdict = None
    verdict_payload: dict = {}
    try:
        verdict_payload = json.loads(proc.stdout.strip().splitlines()[-1])
        verdict = verdict_payload.get("verdict")
    except (ValueError, IndexError, AttributeError):
        pass
    if verdict not in ("webtoon", "paged"):
        print("\nmanga-auto: style is UNCERTAIN — a human/agent must look at the "
              "sample pages and pick the splitter:")
        for sample in verdict_payload.get("sample_images", []):
            print(f"  sample: {sample}")
        print(f"  then run {CLI_NAME} webtoon-split OR page-split with --force-style, "
              f"and re-run manga-auto WITHOUT --url to continue.")
        return 3

    splitter = "webtoon-split" if verdict == "webtoon" else "page-split"
    code, _ = run_stage(splitter, cli_command(
        splitter, "--project-root", str(project_root), *_item_args(args),
        "--work-dir", str(args.work_dir)))
    if code != 0:
        print(f"manga-auto: {splitter} failed — see the log above.")
        return 1

    if gemma_ready():
        code, _ = run_stage("crop-qa", cli_command(
            "crop-qa", "--project-root", str(project_root), *_item_args(args),
            "--work-dir", str(args.work_dir)))
        if code == 3:
            print("\nmanga-auto: crop-qa proposed fixes (commands above). Apply them, "
                  "re-split with the overrides file, then re-run manga-auto.")
            return 3
        if code != 0:
            print("manga-auto: crop-qa failed — review the verify sheets manually "
                  "before continuing.")
            return 1
    else:
        print(f"\n[note] gemma-4 not installed — skipping automated crop QA. Review "
              f"the verify sheets yourself, or `{CLI_NAME} install-tool gemma-4`.")

    if resolve_tool_dir("deepseek-ocr2", required=False) is not None:
        code, _ = run_stage("panel-transcript", cli_command(
            "panel-transcript", "--project-root", str(project_root), *_item_args(args)))
        if code != 0:
            print("manga-auto: panel-transcript failed — transcripts are optional; "
                  "continuing without full OCR.")
    else:
        print("\n[note] deepseek-ocr2 not installed — narration will be grounded on "
              "panel images only.")

    if gemma_ready():
        from mediaconductor.assist.characters import characters_path

        if not characters_path(project_root).is_file():
            code, _ = run_stage("characters --auto-draft", cli_command(
                "characters", "--project-root", str(project_root), *_item_args(args),
                "--work-dir", str(args.work_dir), "--auto-draft"))
            # exit 3 = draft written; anything else just means no registry yet.
        code, _ = run_stage("narrate-auto", cli_command(
            "narrate-auto", "--project-root", str(project_root), *_item_args(args),
            "--work-dir", str(args.work_dir)))
        if code not in (0, 3):
            print("manga-auto: narrate-auto failed — write narration.json per the "
                  "manga-video skill and re-run with --stage build.")
            return 1
    else:
        print(f"\nmanga-auto: gemma-4 not installed — write each <item>/narration.json "
              f"per the manga-video skill (grounded, panel by panel), run "
              f"narration-check + narration-review-sheets, then "
              f"`{CLI_NAME} manga-auto --project-root {project_root} --stage build`.")
        return 3

    print(f"""
manga-auto: PREP COMPLETE — review before building:
  1. crop sheets:      {args.work_dir / ('webtoon_verify' if verdict == 'webtoon' else 'page_verify') / project_root.name}
  2. crop-qa reports:  {args.work_dir / 'crop_qa' / project_root.name}
  3. characters:       {project_root / 'characters.json'} (fix names, set draft:false)
  4. narration sheets: {args.work_dir / 'narration_review' / project_root.name}
     (fix wrong speakers/claims with narration-edit, rerun narration-check)
Then build: {CLI_NAME} manga-auto --project-root {project_root} --stage build""")
    return 3


def build(args) -> int:
    project_root = resolve_project_root(args, None)
    if project_root is None or not project_root.is_dir():
        print("manga-auto: --stage build needs --project-root (or --name).")
        return 2
    roots = ["--project-root", str(project_root),
             "--audio-root", str(args.audio_root),
             "--output-root", str(args.output_root)]
    code, _ = run_stage("video (full pipeline)", cli_command(
        "video", *roots, "--work-dir", str(args.work_dir), *_item_args(args),
        "--tts", args.tts,
        "--build-long-video", "--normalize-audio", "--no-background-music"))
    if code != 0:
        print("manga-auto: video build failed — see the log above.")
        return code
    code, _ = run_stage("video-validate", cli_command(
        "video-validate", *roots, *_item_args(args), "--json"))
    qa_code, _ = run_stage("work-qa", cli_command(
        "work-qa", *roots, "--work-dir", str(args.work_dir), *_item_args(args), "--json"))
    if code != 0 or qa_code != 0:
        print("manga-auto: validation/QA reported problems above — every problem "
              "line carries its fix command; loop until both exit 0.")
        return code or qa_code
    print("\nmanga-auto: BUILD COMPLETE. Inspect the joined video (start/middle/end "
          "frames + audio) before any explicit publish step. Background music, "
          "thumbnails, and upload remain separate commands (see the skill docs).")
    return 0


def main() -> int:
    from mediaconductor.config import PROJECT_ROOT

    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} manga-auto",
        description="One-command manga pipeline: download → style-detect → the correct "
                    "splitter → crop QA → transcript → narration draft (stage: prep), "
                    "then after review: TTS + render + validate (stage: build). "
                    "Never publishes.",
    )
    parser.add_argument("--url", help="MangaDex title URL to download first.")
    parser.add_argument("--name", help="Library folder name (with --url, or to locate "
                                       "an existing library/<name>).")
    parser.add_argument("--project-root", type=Path, default=None,
                        help="Existing project folder (library/<name>); overrides --name.")
    parser.add_argument("--items", nargs="*", help="Item folders, e.g. 01 02 05-08.")
    parser.add_argument("--item-range")
    parser.add_argument("--download-chapters", nargs="*", default=None,
                        help="With --url: chapter tokens for download --chapters "
                             "(default: --all).")
    parser.add_argument("--stage", choices=("prep", "build"), default="prep")
    parser.add_argument("--tts", default="auto")
    parser.add_argument("--work-dir", type=Path, default=PROJECT_ROOT / "work")
    parser.add_argument("--audio-root", type=Path, default=PROJECT_ROOT / "audio")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "output")
    args = parser.parse_args()
    args.work_dir = args.work_dir.resolve()
    args.audio_root = args.audio_root.resolve()
    args.output_root = args.output_root.resolve()

    print(f"manga-auto: workspace {PROJECT_ROOT}")
    code = prep(args) if args.stage == "prep" else build(args)
    emit_result(command="manga-auto", stage=args.stage, exit_code=code,
                review_required=(code == 3))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
