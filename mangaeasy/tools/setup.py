"""mangaeasy.tools.setup — one-command provisioning from a fresh clone.

``mediaconductor setup`` chains everything a new machine needs, in order:

1. Vendored core binaries — ffmpeg/ffprobe, uv, git-lfs into this install's
   own tools dir (``bootstrap-tools``).
2. Hardware detection — NVIDIA GPU present or not.
3. AI tool environments — each into its isolated uv env under
   ``.mangaeasy/tools/`` with model weights, GPU-aware by default:

   - always: ``kokoro-82m`` (CPU TTS — the universal fallback engine)
   - with an NVIDIA GPU: also ``index-tts``, ``magi-v3``, ``deepseek-ocr2``,
     ``z-image-turbo``

4. A final ``doctor`` readiness report.

``--all`` forces every tool regardless of hardware, ``--minimal`` stops after
the core binaries, ``--skip <tool>`` drops individual tools. Re-running is
safe and idempotent: existing tools are updated / partially-downloaded models
resume (``install_tool`` already behaves that way).

Everything lands inside this install's data folder — nothing is scattered on
the host (see ``tool_env()`` cache pinning in tools/external.py).
"""

from __future__ import annotations

import argparse
import sys

from mangaeasy.brand import CLI_NAME
from mangaeasy.tools.hardware import has_nvidia_gpu, nvidia_gpu_name
from mangaeasy.tools.install import TOOLS, InstallError, doctor, install_tool
from mangaeasy.tools.vendored import ensure_core_tools, ensure_vendored_path
from mangaeasy.utils import emit_result

# Installed on every machine — the pipeline always has a working TTS engine.
BASE_TOOLS = ["kokoro-82m"]
# Installed when an NVIDIA GPU is detected (or forced with --all).
GPU_TOOLS = ["index-tts", "magi-v3", "deepseek-ocr2", "z-image-turbo"]

MODE_TOOLS = {
    "manga-video": ["kokoro-82m", "index-tts", "magi-v3", "deepseek-ocr2", "z-image-turbo"],
    "ai-story": ["kokoro-82m", "index-tts", "z-image-turbo"],
    "song-video": ["ace-step", "demucs", "whisperx", "z-image-turbo"],
}


def plan_tools(profile: str, gpu: bool, skip: set[str], mode: str | None = None) -> list[str]:
    """Which tool envs this run will install, in install order."""
    if mode:
        selected = list(MODE_TOOLS[mode])
    elif profile == "minimal":
        selected: list[str] = []
    elif profile == "all":
        selected = list(TOOLS)
    else:  # auto
        selected = BASE_TOOLS + (GPU_TOOLS if gpu else [])
    return [t for t in selected if t not in skip]


def main() -> int:
    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} setup",
        description="Provision this install end to end: core binaries, AI tool "
                    "environments, and model downloads. GPU-aware by default; "
                    "safe to re-run (updates / resumes instead of reinstalling).",
    )
    profile_group = parser.add_mutually_exclusive_group()
    profile_group.add_argument("--all", action="store_true",
                               help="Install every tool env regardless of hardware "
                                    "(large: Z-Image alone downloads ~33 GB).")
    profile_group.add_argument("--minimal", action="store_true",
                               help="Only the core binaries (ffmpeg/uv/git-lfs, ~100 MB); "
                                    "install AI tools later with install-tool.")
    profile_group.add_argument("--mode", choices=tuple(MODE_TOOLS),
                               help="Install only one production mode's isolated dependencies.")
    parser.add_argument("--skip", action="append", default=[], metavar="TOOL",
                        choices=sorted(TOOLS), help="Skip one tool (repeatable).")
    gpu_group = parser.add_mutually_exclusive_group()
    gpu_group.add_argument("--cpu", action="store_true",
                           help="Force CPU torch builds (default: auto-detect).")
    gpu_group.add_argument("--cuda", action="store_true",
                           help="Force CUDA torch builds (default: auto-detect).")
    parser.add_argument("--skip-models", action="store_true",
                        help="Set up the tool envs but defer model downloads to first use.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what this run would install and exit without changing anything.")
    args = parser.parse_args()

    profile = "all" if args.all else "minimal" if args.minimal else "auto"
    gpu = has_nvidia_gpu()
    gpu_mode = "cpu" if args.cpu else "cuda" if args.cuda else "auto"
    tools = plan_tools(profile, gpu, set(args.skip), args.mode)

    gpu_label = nvidia_gpu_name() or ("yes" if gpu else "none")
    print(f"MediaConductor setup — profile: {profile}, mode: {args.mode or 'all'}, NVIDIA GPU: {gpu_label}")
    print("Core binaries: ffmpeg/ffprobe, uv, git-lfs (vendored into this install)")
    print("AI tools this run: " + (", ".join(tools) if tools else "(none — minimal profile)"))
    skipped_gpu = [t for t in GPU_TOOLS if t not in tools and profile == "auto" and not args.mode]
    if skipped_gpu:
        print(f"Skipped (no NVIDIA GPU): {', '.join(skipped_gpu)} — "
              f"install later with `{CLI_NAME} install-tool <name>` or re-run `setup --all`.")

    if args.dry_run:
        emit_result(dry_run=True, profile=profile, mode=args.mode, gpu=gpu, tools=tools)
        return 0

    # 1. Core binaries — everything after this (git clone, uv sync, hf download)
    #    may rely on them, so a failure here is fatal.
    print("\n=== Core binaries ===")
    core = ensure_core_tools(print)
    ensure_vendored_path()  # pick up freshly vendored bins in this process
    if not all(core.values()):
        failed = ", ".join(name for name, ok in core.items() if not ok)
        print(f"\nERROR: core binaries failed to download: {failed}. "
              f"Check the network and re-run `{CLI_NAME} setup`.", file=sys.stderr)
        return 1

    # 2. Tool envs — keep going past individual failures so one flaky download
    #    doesn't waste the multi-GB progress of the others; report at the end.
    statuses: dict[str, str] = {}
    for idx, name in enumerate(tools, start=1):
        print(f"\n=== Tool {idx}/{len(tools)}: {name} ===")
        try:
            install_tool(name, gpu=gpu_mode, skip_model=args.skip_models)
            statuses[name] = "ok"
        except InstallError as exc:
            print(f"[setup] {name} failed: {exc}", file=sys.stderr)
            statuses[name] = "failed"
        except KeyboardInterrupt:
            print(f"\n[setup] interrupted — re-run `{CLI_NAME} setup` to resume.", file=sys.stderr)
            return 1

    # 3. Readiness report.
    report = doctor()
    print("\n=== Setup summary ===")
    print(f"  Tools dir : {report['tools_home']}")
    print(f"  GPU       : {report['gpu_backend']}")
    for name in tools:
        print(f"  {name:14s} {statuses[name]}")
    failures = [name for name, status in statuses.items() if status == "failed"]
    if failures:
        print(f"\nSome tools failed: {', '.join(failures)}. Re-run `{CLI_NAME} setup` "
              f"(resumes where it left off) or `{CLI_NAME} install-tool <name>`.")
    else:
        print(f"\nReady. Orient with: {CLI_NAME} where --json / commands --json / doctor --json")

    emit_result(
        profile=profile,
        mode=args.mode,
        gpu=gpu,
        core=core,
        tools=statuses,
        failures=failures,
        tools_home=report["tools_home"],
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
