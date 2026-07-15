"""mangaeasy.panels.overrides_tool — build webtoon-split overrides from the CLI.

``mangaeasy webtoon-override`` maintains the per-item overrides JSON that
``webtoon-split --overrides`` consumes, resolving every fix against the
ranges manifest so nobody ever computes a merge index by hand again (doing
that by eye shipped one-off merges twice in production):

- ``--merge-at-cut Y``   undo the auto-split cut at stitched y=Y (from a
  cutcheck window label) — resolved to the two base panels meeting at Y.
- ``--merge-panels A,B`` fuse the panels labeled #A..#B in the CURRENT run's
  sheets/windows (1-based ``final`` numbers) — translated to base indices,
  so it works even after earlier overrides were applied.
- ``--split-at Y``       force a cut at stitched y=Y (picked from pixel data;
  applied after merges, so "merge-at-cut + split-at" repositions a bad cut).
- ``--show``             print the file resolved against the manifests.

Overlapping/chained merges are coalesced automatically. The overrides file
is created if missing; re-run ``webtoon-split --overrides <file>`` after
editing, then ``webtoon-cutcheck`` to confirm.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mangaeasy.brand import CLI_NAME
from mangaeasy.utils import emit_result

Y_TOLERANCE = 2


# ---------------------------------------------------------------- pure logic

def coalesce_merges(pairs: list[list[int]]) -> list[list[int]]:
    """Coalesce overlapping/adjacent-chained inclusive [i, j] merge spans."""
    if not pairs:
        return []
    pairs = sorted([list(p) for p in pairs])
    out = [pairs[0]]
    for i, j in pairs[1:]:
        if i <= out[-1][1]:
            out[-1][1] = max(out[-1][1], j)
        else:
            out.append([i, j])
    return out


def resolve_merge_at_cut(base: list[dict], y: int) -> list[int]:
    """Merge pair for the base panels meeting at stitched row y."""
    above = [p for p in base if abs(p["bottom"] - y) <= Y_TOLERANCE]
    if len(above) != 1:
        raise ValueError(
            f"cut y={y}: expected exactly one base panel ending there, found "
            f"{[(p['index'], p['top'], p['bottom']) for p in above]} — check the y "
            f"against the manifest's base list")
    k = base.index(above[0])
    if k + 1 >= len(base):
        raise ValueError(f"cut y={y}: panel ending there is the last base panel")
    below = base[k + 1]
    if abs(below["top"] - y) > Y_TOLERANCE:
        raise ValueError(
            f"cut y={y}: next base panel starts at {below['top']}, not ~{y} — "
            f"that boundary is a dropped gap, not an auto-split cut")
    return [k, k + 1]


def resolve_merge_panels(base: list[dict], final: list[dict],
                         first: int, last: int) -> list[int]:
    """Translate 1-based CURRENT-final panel numbers to a base merge span."""
    if not (1 <= first <= len(final) and 1 <= last <= len(final) and first < last):
        raise ValueError(f"panels {first},{last}: need 1 <= A < B <= {len(final)}")
    top = final[first - 1]["top"]
    bottom = final[last - 1]["bottom"]
    covered = [i for i, p in enumerate(base)
               if min(p["bottom"], bottom) - max(p["top"], top)
               >= 0.5 * (p["bottom"] - p["top"])]
    if len(covered) < 2:
        raise ValueError(
            f"panels {first},{last}: span {top}-{bottom} covers {len(covered)} base "
            f"panel(s) — nothing to merge (already one base panel?)")
    return [covered[0], covered[-1]]


# --------------------------------------------------------------------- CLI

def parse_panels_pair(raw: str) -> tuple[int, int]:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise argparse.ArgumentTypeError(f"expected 'A,B' panel numbers, got {raw!r}")
    return int(parts[0]), int(parts[1])


def parse_args() -> argparse.Namespace:
    from mangaeasy.video_pipeline.common import DEFAULT_PROJECT_ROOT, DEFAULT_WORK_DIR

    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} webtoon-override",
        description="Add merge/split fixes to a webtoon-split overrides file, resolving "
                    "indices against the ranges manifest (no hand-computed indices).",
    )
    parser.add_argument("--file", type=Path, required=True,
                        help="Overrides JSON to create/extend (pass the same file to "
                             "webtoon-split --overrides).")
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--verify-root", type=Path, default=None,
                        help="Where webtoon-split wrote <item>_ranges.json "
                             "(default: <work-dir>/webtoon_verify/<project-name>).")
    parser.add_argument("--item", default=None,
                        help="Item the fixes apply to (required with any fix flag).")
    parser.add_argument("--merge-at-cut", type=int, action="append", default=[],
                        metavar="Y", help="Undo the auto-split cut at stitched y=Y.")
    parser.add_argument("--merge-panels", type=parse_panels_pair, action="append",
                        default=[], metavar="A,B",
                        help="Fuse current panels #A..#B (1-based numbers from the "
                             "latest sheets/cutcheck windows).")
    parser.add_argument("--split-at", type=int, action="append", default=[],
                        metavar="Y", help="Force an extra cut at stitched y=Y.")
    parser.add_argument("--show", action="store_true",
                        help="Print the overrides file resolved against the manifests.")
    return parser.parse_args()


def load_manifest(verify_dir: Path, item: str) -> dict:
    path = verify_dir / f"{item}_ranges.json"
    if not path.is_file():
        raise FileNotFoundError(f"no ranges manifest at {path} — run webtoon-split first")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if "base" not in manifest:
        raise ValueError(
            f"{path} predates the base list — re-run webtoon-split for item {item} "
            f"(without --overrides) to refresh the manifest")
    return manifest


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    verify_dir = (args.verify_root or args.work_dir / "webtoon_verify" / project_root.name).resolve()

    overrides: dict = {}
    if args.file.is_file():
        overrides = json.loads(args.file.read_text(encoding="utf-8-sig"))

    has_fixes = bool(args.merge_at_cut or args.merge_panels or args.split_at)
    if has_fixes:
        if not args.item:
            print("ERROR: --item is required when adding fixes")
            return 2
        try:
            manifest = load_manifest(verify_dir, args.item)
            base, final = manifest["base"], manifest["final"]
            entry = overrides.setdefault(args.item, {})
            merges = [list(p) for p in entry.get("merge", [])]
            for y in args.merge_at_cut:
                pair = resolve_merge_at_cut(base, y)
                merges.append(pair)
                print(f"[{args.item}] merge-at-cut y={y} -> merge {pair} "
                      f"(base {base[pair[0]]['top']}-{base[pair[0]]['bottom']} + "
                      f"{base[pair[1]]['top']}-{base[pair[1]]['bottom']})")
            for first, last in args.merge_panels:
                span = resolve_merge_panels(base, final, first, last)
                merges.append(span)
                print(f"[{args.item}] merge-panels {first},{last} -> merge {span}")
            if merges:
                entry["merge"] = coalesce_merges(merges)
            for y in args.split_at:
                splits = entry.setdefault("split_at", [])
                if y not in splits:
                    splits.append(y)
                    splits.sort()
                print(f"[{args.item}] split-at y={y}")
        except (ValueError, FileNotFoundError) as exc:
            print(f"ERROR: {exc}")
            return 1
        args.file.parent.mkdir(parents=True, exist_ok=True)
        args.file.write_text(json.dumps(overrides, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.file}")
        print(f"apply with: {CLI_NAME} webtoon-split --project-root {args.project_root} "
              f"--items {args.item} --overrides {args.file}, then re-run webtoon-cutcheck")

    if args.show or not has_fixes:
        for item, entry in sorted(overrides.items()):
            print(f"[{item}]")
            try:
                base = load_manifest(verify_dir, item)["base"]
            except (ValueError, FileNotFoundError):
                base = None
            for pair in entry.get("merge", []):
                detail = ""
                if base and 0 <= pair[0] < len(base) and 0 <= pair[-1] < len(base):
                    detail = (f"  ({base[pair[0]]['top']}"
                              f"-{base[pair[-1]]['bottom']} in strip)")
                print(f"  merge {pair}{detail}")
            for y in entry.get("split_at", []):
                print(f"  split_at y={y}")
            if entry.get("replace"):
                print(f"  replace: {len(entry['replace'])} range(s)")

    emit_result(command="webtoon-override", file=args.file,
                items={k: v for k, v in overrides.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
