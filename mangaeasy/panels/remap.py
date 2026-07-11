"""mangaeasy.panels.remap — carry narration + audio across a re-crop.

Re-running ``webtoon-split`` renumbers every panel, which silently orphans the
item's ``narration.json`` and per-panel WAVs (both keyed by panel filename).
``mangaeasy panels-remap`` repairs that without re-narrating or re-running TTS:

1. locate each OLD panel's span in the stitched source strip (the old crops
   are archived under ``<item>/old/run_NNNN/panels`` by webtoon-split) via a
   row-signature search on the most distinctive block of rows;
2. map old panels to NEW panels (from the current ranges manifest) by
   interval overlap — this survives merges, splits and shifted boundaries;
3. with ``--apply``: rewrite ``narration.json`` in the new numbering (texts
   carried verbatim; merged panels get their texts joined in reading order),
   rebuild the audio dir (copy 1:1/shifted WAVs, wave-concat merged WAVs),
   and restore special panels (hooks/CTA copies) whose files were archived
   with the old panels dir.

Without ``--apply`` it is a dry run: it writes ``<item>_map.json`` plus a
review list and prints a summary. Panels classified ``shift`` (boundary
moved) and every merge should be visually reviewed afterwards —
``narration-review-sheets`` renders panel+text pairs for exactly that.

Verified in production (2026-07): 546/546 narration texts carried across a
7-chapter re-crop with zero orphans and zero TTS regeneration.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import wave
from pathlib import Path

from mangaeasy.utils import archive_before_overwrite, emit_result

SIG_COLS = 16
NEEDLE_ROWS = 64
MATCH_ERR_LIMIT = 60.0


# ---------------------------------------------------------------- pure logic

def overlap(a_top: int, a_bottom: int, b_top: int, b_bottom: int) -> int:
    return min(a_bottom, b_bottom) - max(a_top, b_top)


def map_spans(old_spans: list[dict], new_spans: list[dict], *,
              min_frac: float = 0.5, clean_frac: float = 0.9) -> tuple[list[dict], list[str]]:
    """Map located old panel spans onto new panel spans by interval overlap.

    Both lists need ``top``/``bottom`` ints and a ``file`` name. Returns
    (mapping, orphans): one mapping entry per new span with its constituent
    old panels (reading order) and a class — ``one2one`` (mutual overlap >=
    clean_frac), ``merge`` (2+ constituents), ``shift`` (single constituent,
    partial overlap) or ``none``. Orphans are old panels no new span claimed.
    """
    mapping: list[dict] = []
    used: set[str] = set()
    for np_ in new_spans:
        new_h = np_["bottom"] - np_["top"]
        cons = []
        for s in old_spans:
            ov = overlap(s["top"], s["bottom"], np_["top"], np_["bottom"])
            if ov <= 0:
                continue
            frac_old = ov / max(1, s["bottom"] - s["top"])
            frac_new = ov / max(1, new_h)
            if frac_old >= min_frac or frac_new >= min_frac:
                cons.append({"old": s["file"], "frac_old": round(frac_old, 3),
                             "frac_new": round(frac_new, 3)})
                used.add(s["file"])
        if len(cons) == 1 and cons[0]["frac_old"] >= clean_frac and cons[0]["frac_new"] >= clean_frac:
            klass = "one2one"
        elif len(cons) >= 2:
            klass = "merge"
        elif len(cons) == 1:
            klass = "shift"
        else:
            klass = "none"
        mapping.append({"new": np_["file"], "top": np_["top"], "bottom": np_["bottom"],
                        "class": klass, "constituents": cons})
    orphans = [s["file"] for s in old_spans if s["file"] not in used]
    return mapping, orphans


def is_regular_panel(stem: str, prefix: str) -> bool:
    """True for split-produced names (<prefix><digits>); hooks/CTA copies are not."""
    return re.fullmatch(re.escape(prefix) + r"\d+", stem) is not None


# ------------------------------------------------------------ span location

def _numeric_key(path: Path) -> list:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", path.stem)]


def _stitch_signature(source_dir: Path):
    import numpy as np
    from PIL import Image

    pages = sorted(
        (p for p in source_dir.iterdir()
         if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}),
        key=_numeric_key,
    )
    imgs = [Image.open(p).convert("L") for p in pages]
    width = max(im.width for im in imgs)
    parts = []
    for im in imgs:
        h = round(im.height * width / im.width)
        parts.append(np.asarray(im.resize((width, h)), dtype=np.float32))
    strip = np.vstack(parts)
    return _row_signature(strip), strip.shape[0], width


def _row_signature(arr):
    import numpy as np
    cols = np.array_split(np.arange(arr.shape[1]), SIG_COLS)
    return np.stack([arr[:, c].mean(axis=1) for c in cols], axis=1)


def _locate(panel_sig, strip_sig, lo: int, hi: int) -> tuple[int, float]:
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view

    h = panel_sig.shape[0]
    blk = min(NEEDLE_ROWS, h)
    if h > blk:
        var = panel_sig.std(axis=1)
        score = np.convolve(var, np.ones(blk), mode="valid")
        boff = int(score.argmax())
    else:
        boff = 0
    needle = panel_sig[boff:boff + blk]
    lo = max(0, lo + boff)
    hi = min(strip_sig.shape[0] - blk, hi + boff)
    seg = strip_sig[lo:hi + blk]
    wins = sliding_window_view(seg, (blk, SIG_COLS)).reshape(-1, blk * SIG_COLS)
    d = ((wins - needle.reshape(-1)) ** 2).mean(axis=1)
    best = int(d.argmin())
    return lo + best - boff, float(d[best])


def locate_old_spans(old_files: list[Path], source_dir: Path) -> tuple[list[dict], int]:
    import numpy as np
    from PIL import Image

    strip_sig, strip_h, width = _stitch_signature(source_dir)
    spans: list[dict] = []
    prev_bottom = 0
    for f in old_files:
        im = Image.open(f).convert("L")
        if im.width != width:
            im = im.resize((width, round(im.height * width / im.width)))
        psig = _row_signature(np.asarray(im, dtype=np.float32))
        h = psig.shape[0]
        lo, hi = max(0, prev_bottom - 2000), min(strip_h - h, prev_bottom + 15000)
        y0, err = _locate(psig, strip_sig, lo, hi)
        if err > MATCH_ERR_LIMIT:  # bad local match -> global retry
            y0, err = _locate(psig, strip_sig, 0, strip_h - h)
        spans.append({"file": f.name, "top": y0, "bottom": y0 + h, "err": round(err, 1)})
        prev_bottom = y0 + h
    return spans, strip_h


# ------------------------------------------------------------------- apply

def concat_wavs(sources: list[Path], dest: Path) -> None:
    params = None
    frames = []
    for s in sources:
        with wave.open(str(s), "rb") as r:
            p = r.getparams()
            key = (p.nchannels, p.sampwidth, p.framerate)
            if params is None:
                params, header = key, p
            elif key != params:
                raise ValueError(f"WAV params differ: {s} {key} vs {params}")
            frames.append(r.readframes(r.getnframes()))
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(header.nchannels)
        w.setsampwidth(header.sampwidth)
        w.setframerate(header.framerate)
        for f in frames:
            w.writeframes(f)


def default_old_run(item_dir: Path) -> Path | None:
    runs = sorted((item_dir / "old").glob("run_*")) if (item_dir / "old").exists() else []
    with_panels = [r for r in runs if (r / "panels").is_dir()]
    return with_panels[-1] if with_panels else None


def apply_item(item_dir: Path, mapping: list[dict], old_panels: Path,
               audio_item_dir: Path, prefix: str) -> dict:
    old_narr = json.loads((item_dir / "narration.json").read_text(encoding="utf-8-sig"))
    text_by_old = {e["image"]: e["narration"] for e in old_narr}

    audio_item_dir.mkdir(parents=True, exist_ok=True)
    arch = audio_item_dir / "old" / "remap_source"
    arch.mkdir(parents=True, exist_ok=True)
    for w in list(audio_item_dir.glob("*.wav")):
        shutil.move(str(w), arch / w.name)

    stats = {"carried": 0, "merged": 0, "unnarrated": 0, "missing_wav": []}
    new_entries: list[dict] = []
    for entry in mapping:
        cons = [c for c in entry["constituents"] if c["old"] in text_by_old]
        if not cons:
            stats["unnarrated"] += 1
            continue
        stem = Path(entry["new"]).stem
        srcs = [arch / (Path(c["old"]).stem + ".wav") for c in cons]
        missing = [s.name for s in srcs if not s.exists()]
        if missing:
            stats["missing_wav"] += missing
        elif len(srcs) == 1:
            shutil.copy2(srcs[0], audio_item_dir / f"{stem}.wav")
            stats["carried"] += 1
        else:
            concat_wavs(srcs, audio_item_dir / f"{stem}.wav")
            stats["merged"] += 1
        new_entries.append({
            "image": entry["new"],
            "narration": " ".join(text_by_old[c["old"]].strip() for c in cons),
        })

    # Special panels (hook/CTA physical copies): restore image files that were
    # archived with the old panels dir, carry their entries and WAVs verbatim.
    for e in old_narr:
        stem = Path(e["image"]).stem
        if is_regular_panel(stem, prefix):
            continue
        panel_file = item_dir / "panels" / e["image"]
        if not panel_file.exists() and (old_panels / e["image"]).exists():
            shutil.copy2(old_panels / e["image"], panel_file)
        src = arch / f"{stem}.wav"
        if src.exists():
            shutil.copy2(src, audio_item_dir / f"{stem}.wav")
        new_entries.append(dict(e))
        stats["carried"] += 1
    if (item_dir / "intro.json").exists():
        for e in json.loads((item_dir / "intro.json").read_text(encoding="utf-8-sig")):
            stem = Path(e["image"]).stem
            panel_file = item_dir / "panels" / e["image"]
            if not panel_file.exists() and (old_panels / e["image"]).exists():
                shutil.copy2(old_panels / e["image"], panel_file)
            src = arch / f"{stem}.wav"
            if src.exists():
                shutil.copy2(src, audio_item_dir / f"{stem}.wav")

    archived = archive_before_overwrite(item_dir / "narration.json")
    if archived:
        print(f"[{item_dir.name}] previous narration archived: {archived}")
    (item_dir / "narration.json").write_text(
        json.dumps(new_entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    stats["entries"] = len(new_entries)
    return stats


# --------------------------------------------------------------------- CLI

def parse_args() -> argparse.Namespace:
    from mangaeasy.video_pipeline.common import DEFAULT_AUDIO_ROOT, DEFAULT_PROJECT_ROOT, DEFAULT_WORK_DIR

    parser = argparse.ArgumentParser(
        prog="mangaeasy panels-remap",
        description="After a re-crop, map archived old panels to the new crops and carry "
                    "narration + audio over (dry run by default; --apply to write).",
    )
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--items", nargs="*")
    parser.add_argument("--item-range")
    parser.add_argument("--audio-root", type=Path, default=DEFAULT_AUDIO_ROOT)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--verify-root", type=Path, default=None,
                        help="Where webtoon-split wrote <item>_ranges.json "
                             "(default: <work-dir>/webtoon_verify/<project-name>).")
    parser.add_argument("--source-subdir", default="download")
    parser.add_argument("--old-run", default=None,
                        help="Archive run under <item>/old/ holding the panels the CURRENT "
                             "narration was written against (e.g. run_0002). Default: the "
                             "newest run with a panels dir — pass it explicitly if the item "
                             "was re-cropped more than once.")
    parser.add_argument("--apply", action="store_true",
                        help="Rewrite narration.json and rebuild the audio dir. Without this "
                             "flag only the mapping report is written.")
    return parser.parse_args()


def main() -> int:
    from mangaeasy.video_pipeline.common import item_dirs, merge_item_selection

    args = parse_args()
    project_root = args.project_root.resolve()
    selected = item_dirs(project_root, merge_item_selection(args.items, args.item_range))
    if not selected:
        print(f"[FATAL] No item folders found under {project_root}")
        return 1
    verify_dir = (args.verify_root or args.work_dir / "webtoon_verify" / project_root.name).resolve()
    report_dir = (args.work_dir / "remap" / project_root.name).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, dict] = {}
    failed = False
    for i, item_dir in enumerate(selected, 1):
        print(f"MANGAEASY_PROGRESS {i}/{len(selected)}", flush=True)
        item = item_dir.name
        manifest_path = verify_dir / f"{item}_ranges.json"
        old_run = (item_dir / "old" / args.old_run) if args.old_run else default_old_run(item_dir)
        if not manifest_path.is_file() or old_run is None:
            print(f"[{item}] missing ranges manifest or archived old panels — skipped")
            results[item] = {"error": "missing manifest or old run"}
            failed = True
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        prefix = manifest.get("prefix") or f"ch{item}_"
        old_files = [f for f in sorted((old_run / "panels").glob("*.*"), key=_numeric_key)
                     if is_regular_panel(f.stem, prefix)]
        spans, _ = locate_old_spans(old_files, item_dir / args.source_subdir)
        bad = [s for s in spans if s["err"] > MATCH_ERR_LIMIT]
        mapping, orphans = map_spans(spans, manifest["final"])
        counts: dict[str, int] = {}
        for m in mapping:
            counts[m["class"]] = counts.get(m["class"], 0) + 1
        review = [m["new"] for m in mapping if m["class"] in ("shift", "merge")]
        (report_dir / f"{item}_map.json").write_text(
            json.dumps({"item": item, "old_run": old_run.name, "spans": spans,
                        "mapping": mapping, "orphans": orphans, "review": review},
                       indent=1), encoding="utf-8")
        print(f"[{item}] old_run={old_run.name} {counts} orphans={orphans} "
              f"bad_locates={[(s['file'], s['err']) for s in bad]}", flush=True)
        results[item] = {"old_run": old_run.name, "counts": counts,
                         "orphans": orphans, "review": review}
        if orphans or bad:
            failed = True
            print(f"[{item}] NOT SAFE to --apply: resolve orphans/bad locates first")
            continue
        if args.apply:
            audio_item_dir = args.audio_root.resolve() / project_root.name / item
            stats = apply_item(item_dir, mapping, old_run / "panels", audio_item_dir, prefix)
            results[item]["applied"] = stats
            print(f"[{item}] applied: {stats}", flush=True)

    if args.apply:
        print("Review every 'shift' and 'merge' panel next: "
              "`mangaeasy narration-review-sheets` renders panel+text pairs for that, "
              "then run video-audio-audit and rebuild with `mangaeasy video "
              "--overwrite-video`.")
    emit_result(command="panels-remap", report_dir=report_dir,
                applied=args.apply, items=results)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
