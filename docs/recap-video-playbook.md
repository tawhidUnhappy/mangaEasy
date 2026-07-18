# Manga recap video playbook — for AI agents

This is the exact, end-to-end recipe used to produce a full YouTube manga
recap video autonomously with MediaConductor (reference production: *Irozuku
Monochrome* ch. 1 — 9:05, 96 narrated panels, IndexTTS voice clone,
uploaded with thumbnail/title/description/chapters). Follow it top to
bottom. Everything here was learned the hard way in a real production;
the **bold warnings are the places it actually went wrong**.

Read `docs/manga-video-guide.md` (manga-only CLI contract) and the repo
`CLAUDE.md` first.
All commands run from the install root (`uv run mediaconductor ...` in a dev
checkout).

**Working style — go idle between long steps.** Downloading, cropping, OCR,
audio/render, image generation, and upload each take minutes to tens of
minutes. Launch each as a background job and then **stop and wait for the
completion notification** instead of polling or sleeping in a loop — it is the
biggest compute saver on a full run. GPU tools block-buffer stdout (their logs
stay empty until they exit), so check progress from the filesystem (crops /
`transcript.json` filling in, output files appearing) rather than tailing the
log. Transcripts and crops land per chapter, so you can start writing narration
for a finished chapter while the GPU works through the rest.

---

## Phase 0 — Environment

```bash
mediaconductor where --json      # resolved paths; run this first
mediaconductor doctor --json     # ffmpeg/GPU/tool status
mediaconductor tools --json      # which external tool envs are installed
```

Install what's missing:

```bash
mediaconductor install-tool magi-v3       # panel detection (needed for paged manga)
mediaconductor install-tool index-tts     # default recap TTS: voice clone, slow, best quality
mediaconductor install-tool z-image-turbo # generated thumbnails; auto GPU/CPU strategy
# Kokoro installs the same way if absent: mediaconductor install-tool kokoro-82m
```

For YouTube, place one Desktop-app client JSON at the shared path reported by
`mediaconductor youtube-profiles --json`. Before publishing, select an explicit
profile and run `mediaconductor youtube-status --profile <profile> --verify`;
the live call opens browser consent when that profile needs it. See
`docs/youtube.md` for setup, profile isolation, and token permissions.

## Phase 1 — Download the chapter

Set the `download` block of `config.json` (project root): MangaDex title
URL, chapter number, `translated_language`. Then:

```bash
mediaconductor download
```

Put/keep the raw pages in `library/<Project>/<item>/download/` (item =
zero-padded chapter, e.g. `01`). Page files are `01_00.jpg … 01_NN.jpg`.

`download` also writes/updates `library/<Project>/manga.json` — the manga's
source record (MangaDex title URL, canonical title, per-chapter download
info). Read it later when you need the manga's link or the official title,
e.g. for the description's credits / "support the official release" section
(`mediaconductor library-list --json` includes it as each project's `manga`
field).

## Phase 2–3 for webtoons — `webtoon-split`, then clear every flag

**This applies to vertical-strip webtoons** (one endless scroll with
gutter-separated panels). Paged manga: skip to the MAGI phases below.

One command replaces detection + cropping + verification-sheet generation:

```bash
mediaconductor webtoon-split --project-root library/<Project> --item-range 01-19
```

Per item it stitches `download/` into one tall strip, splits it at gutters
(same detection code path as `gutter-split`), then applies two fixups the
raw gutter pass reliably needs on real webtoons:

- **Auto-split** — any "panel" taller than 2.2× width is re-cut at the
  quietest row near even split points. A single missed gutter otherwise
  produces a 10,000-px panel that renders unreadably small in a video.
- **Gap rescue** — dropped gaps whose interior still contains content
  (scene-break captions like "ONE HOUR LATER…" sitting on gutter-colored
  background) are attached to the following panel so no story text is lost.

Crops land in `library/<Project>/<item>/panels/ch<item>_###.jpg` (an
existing panels folder is archived to `<item>/old/run_NNNN/` first), and
verification images in `work/webtoon_verify/<Project>/`:

- `NN_sheet_K.png` — numbered contact sheets; suspects get a red `!!` label.
- `NN_strip_K.png` — the downscaled strip with green panel boxes, blue
  auto-cut lines, and red dropped rows.

**Clear every flag visually before writing narration — on full-resolution
windows, not contact sheets.** A shipped recap had to be fully redone because
its crops were judged on downscaled sheets (half panels, fused stuck-together
panels, sliced speech bubbles). The pass that catches them:

```bash
mediaconductor webtoon-cutcheck --project-root library/<Project> --item-range 01-19
```

It reads the `<item>_ranges.json` manifests webtoon-split wrote and renders a
±650 px full-res window around every forced auto-split cut and every short
panel, montaged into sheets under `work/cutcheck/<Project>/`. Read every
sheet; verdicts: **FIX** when a cut passes through a figure or speech bubble
or a short panel is a bubble/SFX fragment (merge it toward its bubble-mate);
**ACCEPT** for background/effect-art cuts, bordered thin scenery panels and
scanlator banners (skip those in narration). Production-verified benign
patterns: a thin `#3`-ish sliver near the top = scanlator credit banner; a
trailing drop of h≈765–1054 = "we're recruiting" promo; thin bright slivers
mid-chapter = SFX calligraphy.

Collect every FIX into one overrides file with `webtoon-override` — it
resolves all indices against the manifest, so never compute them by hand:

```bash
mediaconductor webtoon-override --file work/overrides.json \
    --project-root library/<Project> --item 07 --merge-at-cut 23140
mediaconductor webtoon-override --file work/overrides.json \
    --project-root library/<Project> --item 12 --merge-panels 5,6
# reposition a bad cut = merge across it + force the right y:
mediaconductor webtoon-override --file work/overrides.json \
    --project-root library/<Project> --item 02 --merge-at-cut 42186 --split-at 42394
```

(Under the hood: `merge [[i, j]]` = 0-based positions in the manifest's
`base` list — stable across override iterations; `split_at` = absolute
stitched y applied after merges, fragments under 20 px dropped; pick split
y-values from pixel data, not scaled screenshots. `--show` prints the file
resolved against the manifests.)

Then re-run `webtoon-cutcheck` to confirm the fixed locations, and if
narration already existed for the old numbering, carry it over with
`panels-remap` (see `docs/operate/crop-verify-narrate.md`) instead of
re-narrating.

Webtoon panel naming is `ch{item}_{i:03d}.jpg` — narration.json keys on
these filenames. Chapters from different scanlators differ in boilerplate:
check the first sheet of each group for leading credit/cover pages (skip
them in narration) and the last sheet for trailing promo panels.

## Phase 2 — Panel detection (MAGI v3, paged manga)

**This applies to paged manga.** Vertical webtoons don't need MAGI — use
`mediaconductor webtoon-split` (previous section) instead.

The repo ships a single-image adapter
(`mediaconductor/assets/tools/detect_magi.py`, copied into the tool env by
`install-tool`), but it reloads the model per call. For a whole chapter,
load the model **once** and loop. Find the tool env via
`mediaconductor tools --json`, then run this with the env's own python
(`<tool dir>/.venv/Scripts/python.exe` on Windows):

```python
"""batch_detect.py <pages dir> <detections.json> — MAGI v3, model loaded once."""
import json, sys
from pathlib import Path
import numpy as np, torch
from PIL import Image
from transformers import AutoModelForCausalLM, AutoProcessor

MODEL_ID = "ragavsachdeva/magiv3"
src_dir, out_file = Path(sys.argv[1]), Path(sys.argv[2])
device = "cuda" if torch.cuda.is_available() else "cpu"
dtype = torch.float16 if device == "cuda" else torch.float32
processor = AutoProcessor.from_pretrained(MODEL_ID, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_ID, torch_dtype=dtype, trust_remote_code=True, attn_implementation="eager"
).to(device).eval()

results = {}
pages = sorted(p for p in src_dir.iterdir()
               if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
for i, page in enumerate(pages, 1):
    img = Image.open(page).convert("RGB")
    with torch.no_grad():
        dets = model.predict_detections_and_associations(
            [np.array(img, dtype=np.uint8)], processor)
    panels = [[float(v) for v in box] for box in (dets[0].get("panels", []) if dets else [])]
    results[page.name] = {"size": [img.width, img.height], "panels": panels}
    print(f"{i}/{len(pages)} {page.name}: {len(panels)} panels", flush=True)
out_file.write_text(json.dumps(results, indent=1), encoding="utf-8")
```

**Known MAGI env pins (production-verified).** The stock env may fail;
fix with `uv pip install` *into the magi-v3 env*:

- `transformers==4.48.3` — newer (4.57.x) breaks Florence2:
  `generate` disappears and `_supports_sdpa` raises.
- `attn_implementation="eager"` is required in `from_pretrained` (above).
- Three undeclared deps: `pytorch_metric_learning matplotlib shapely`.

## Phase 3 — Crop panels, then VERIFY EVERY PAGE VISUALLY

**Never trust MAGI's boxes.** In the reference production it was wrong on
4 of 61 pages: two pages with vertically merged panels, one box covering
the whole page, one missed mini-column on a two-page spread. Wrong crops
poison everything downstream (narration written against images the viewer
never sees correctly).

Crop with the same reading-order algorithm the app uses
(`_manga_reading_order()` in `mediaconductor/panels/ai.py` — RTL band-overlap
topological sort). Working script (drop in a scratch dir):

```python
"""crop_panels.py [page.jpg ...] — crop detections.json into panels/ + overlay sheets.
Manual fixes go in overrides.json: {"01_09.jpg": [[x1,y1,x2,y2], ...]} fully
replaces MAGI's boxes for that page. Args = re-crop only those pages."""
import json, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

SCRATCH = Path(__file__).parent
DOWNLOAD_DIR = Path(r"<project>/library/<Project>/<item>/download")
PANELS_DIR = Path(r"<project>/library/<Project>/<item>/panels")
CHAPTER, RTL = 1, True

def clamp_box(raw, W, H):
    try: x1, y1, x2, y2 = (int(v) for v in raw[:4])
    except (TypeError, ValueError): return None
    x1, y1 = max(0, min(x1, W)), max(0, min(y1, H))
    x2, y2 = max(0, min(x2, W)), max(0, min(y2, H))
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2} if x2 > x1 and y2 > y1 else None

def reading_order(boxes):  # mirrors mediaconductor/panels/ai.py
    if len(boxes) <= 1: return list(boxes)
    cy = lambda b: (b["y1"] + b["y2"]) / 2; cx = lambda b: (b["x1"] + b["x2"]) / 2
    n = len(boxes); adj = {i: [] for i in range(n)}; deg = dict.fromkeys(range(n), 0)
    for i in range(n):
        for j in range(n):
            if i == j: continue
            A, B = boxes[i], boxes[j]
            overlapY = max(0, min(A["y2"], B["y2"]) - max(A["y1"], B["y1"]))
            if overlapY > 0.3 * min(A["y2"] - A["y1"], B["y2"] - B["y1"]):
                before = (cx(A) > cx(B)) if RTL else (cx(A) < cx(B))
            else:
                before = cy(A) < cy(B)
            if before: adj[i].append(j); deg[j] += 1
    out, seen = [], set()
    while len(out) < n:
        cands = [i for i in range(n) if i not in seen and deg[i] == 0]
        if not cands:
            left = [i for i in range(n) if i not in seen]
            m = min(deg[i] for i in left); cands = [i for i in left if deg[i] == m]
        cands.sort(key=lambda i: (int(cy(boxes[i]) // 10), -cx(boxes[i]) if RTL else cx(boxes[i])))
        best = cands[0]; seen.add(best); out.append(boxes[best])
        for nb in adj[best]: deg[nb] -= 1
    return out

only = set(sys.argv[1:])
detections = json.loads((SCRATCH / "detections.json").read_text(encoding="utf-8"))
ovr_path = SCRATCH / "overrides.json"
overrides = json.loads(ovr_path.read_text(encoding="utf-8")) if ovr_path.exists() else {}
PANELS_DIR.mkdir(parents=True, exist_ok=True)
(SCRATCH / "overlays").mkdir(exist_ok=True)
font = ImageFont.truetype("arialbd.ttf", 64)
for page_name in sorted(detections):
    if only and page_name not in only: continue
    page_no = int(Path(page_name).stem.split("_")[1])
    img = Image.open(DOWNLOAD_DIR / page_name).convert("RGB")
    boxes = [b for raw in overrides.get(page_name, detections[page_name]["panels"])
             if (b := clamp_box(raw, *img.size))]
    boxes = reading_order(boxes or [{"x1": 0, "y1": 0, "x2": img.width, "y2": img.height}])
    for old in PANELS_DIR.glob(f"{CHAPTER:02d}_{page_no:02d}_*.png"): old.unlink()
    overlay = img.copy(); draw = ImageDraw.Draw(overlay)
    for k, b in enumerate(boxes, 1):
        img.crop((b["x1"], b["y1"], b["x2"], b["y2"])).save(
            PANELS_DIR / f"{CHAPTER:02d}_{page_no:02d}_{k:02d}.png")
        draw.rectangle([b["x1"], b["y1"], b["x2"], b["y2"]], outline=(255, 0, 0), width=8)
        draw.text((b["x1"] + 14, b["y1"] + 10), str(k), fill=(255, 0, 0), font=font,
                  stroke_width=4, stroke_fill=(255, 255, 255))
    overlay.thumbnail((900, 900)); overlay.save(SCRATCH / "overlays" / f"{Path(page_name).stem}.png")
    print(f"{page_name}: {len(boxes)} panels", flush=True)
```

Then the non-negotiable step: **open and look at every overlay sheet**,
page by page. For each page check (a) every panel has a box, (b) no two
panels share a box, (c) the numbers follow manga reading order
(right→left inside a row, top→bottom across rows — including landscape
spreads), (d) no speech bubble is clipped at a box edge. Fix bad pages by
writing pixel boxes into `overrides.json` and re-running the script with
just those page names. **Overlapping override boxes are fine and often
correct** — for diagonal panel borders, overlap beats clipping a bubble.

Panel naming convention (everything downstream keys on it):
`{chapter:02d}_{page:02d}_{panel:02d}.png` in
`library/<Project>/<item>/panels/`.

## Phase 4 — Read the entire chapter before writing anything

Read every page in order (the panel crops or the raw pages). You are
about to write ~100 narration beats; you cannot hook viewers on a story
you skimmed. While reading, note:

- The 3–5 most shocking/funny panels — hook material.
- Character names, the central irony, the cliffhanger.
- **Panels that are NOT YouTube-safe**: explicit dialogue in bubbles
  (profanity/sexual lines are common in "suggestive"-rated manga),
  risqué imagery, and the credits/scanlator page. List them; they are
  excluded in Phase 5, and they must never appear in the thumbnail.

## Phase 4.5 — OCR the bubbles (`panel-transcript`) — OPTIONAL

```bash
mediaconductor install-tool deepseek-ocr2   # one-time
mediaconductor panel-transcript --project-root library/<Project> --item-range 01-07
```

Writes `<item>/transcript.json` — every panel's bubble/caption text, shown as
a cross-check column on `narration-review-sheets`. **This step is optional and
the narrating agent decides.** Honest cost/benefit from production use: a
vision agent reads the bubbles directly off the panel crops while writing each
line, so the transcript adds no new information most of the time, costs a long
GPU run, and its output on stylized SFX/calligraphy is noisy enough that
disagreements usually resolve in favor of the panel. Run it when bubble text
is small/dense/blurry, when character-name spellings need pinning down, or
when whoever writes narration cannot see images; skip it otherwise. The
narration-quality incidents this phase historically fixed (wrong speakers,
multi-panel summaries, paraphrase drift) were actually fixed by the **per-panel
writing discipline + review-sheet pass** in Phases 4–5 — not by the OCR file
itself. All later gates (work-status/work-qa, review sheets, video build) work
with or without `transcript.json`; only a transcript that exists but is
half-filled is flagged, as an interrupted run to finish or delete.

## Phase 5 — Write `narration.json`

Format (`library/<Project>/<item>/narration.json`):

```json
[{"image": "01_04_01.png", "narration": "One sentence or three. Present tense."}]
```

**Grounding rules (each traces to real viewer complaints):**

- **One beat per panel** — the line covers what is visible in THAT panel.
  Spread story summary across consecutive lines, never smear it over one
  panel the viewer is staring at.
- **Anchor paraphrase to the transcript** — reword for voice and pacing, but
  the meaning must match that panel's OCR text; when the paraphrase reads
  awkward, a trimmed quote is better.
- **Attribute speakers from the panel** — who is on-panel, whose bubble
  (tail) is it? Unsure → don't name the speaker, narrate around it.
- **Punctuation-only lines are unspeakable** — `"?!"` becomes a ~0.03 s WAV;
  give reaction panels a real line (`video-check` flags these).

Structure that worked (96 entries ≈ 9–11 min depending on TTS):

1. **Cold-open hook, ~25–30 s** — 4 of the most absurd panels from *later*
   in the chapter, narrated as escalating questions, then "Let's rewind."
   The official mechanism for this is `intro.json` (same shape, prepended
   automatically by `load_narration()`); putting hook entries at the top
   of `narration.json` works too.
2. **Acts** — setup → inciting incident → disaster → escalation → climax,
   each act ending on a mini-cliffhanger sentence.
3. **CTA outro** on a striking panel (a color page if the chapter has
   one): ask a binary question for comments, ask for the subscribe.

Rules learned in production:

- **The cold-open replays whatever panels it shows.** `intro.json` is
  prepended before the chapter's `narration.json`, so if a panel file appears
  in both, the viewer sees it in the hook and then *again* in-context — the
  "why does the start repeat?" complaint. If you do **not** want that replay,
  give the intro panels the chapter's `narration.json` does not use (or drop
  those panels from `narration.json`). `narration-check` now fails when the
  same panel file is in both. (An intentional teaser-then-payoff replay is the
  one case you'd keep it — pair it with the renamed-copy trick below so at
  least the two showings get their own audio.)
- **Audio is keyed by image stem.** If the hook (or the CTA) reuses a story
  panel, the two entries would share one WAV. Make renamed *physical copies*
  and reference those: hook panels into a page-`00` namespace
  (`01_00_01.png`, `01_00_02.png`, …); the CTA panel into a page number
  *past the last real page* (e.g. `01_74_01.png` when the chapter ends at
  page 72) so it can't collide with a story panel's stem either.
- Skip the unsafe panels from Phase 4 entirely; keep plot-critical
  borderline panels brief and frame them as comedy/panic, never salacious.
- Style: present tense, short punchy sentences, escalation words,
  callbacks to earlier lines, name the antagonist. One entry ≈ 2
  sentences ≈ 5–7 s of TTS.

Validate inputs before burning GPU time:

```bash
mediaconductor video-check --project-root library/<Project> --items 01 --json
```

When you deliberately narrate a subset of panels (the normal case — hook/CTA
copies plus only the story-carrying panels), `video-check` returns
`"ok": false` with "Narration count does not match panel count" / "Panels
not listed in narration" warnings. **That is expected** — unlisted panels
are simply unused, and the pipeline renders only the panels named in
`narration.json`. The pre-build checks that actually matter: the JSON
parses, no two entries share an image stem, and every referenced image
exists on disk. (After building, the warnings that do matter are
audio-related — missing audio for a *referenced* entry; see Phase 7.)

**Then run the semantic pass — this is not optional:**

```bash
mediaconductor narration-review-sheets --project-root library/<Project> --item-range 01-07
```

Read every sheet (panel + narration + OCR side by side) and verify the four
grounding rules above per panel. Fix each bad line in one command (no JSON
editing; the stale WAV is pruned so the next audio run regenerates it):

```bash
mediaconductor narration-edit --project-root library/<Project> --item 01 \
    --set ch01_042.jpg "Rewritten line." --prune-audio
```

## Phase 6 — Build the video

One command runs audio → 8 ms fade derivatives → render → join → BGM → one
final whole-mix normalize:

```bash
# IndexTTS voice clone (default, best quality; leave gpu-workers at default):
mediaconductor video --project-root library/<Project> --items 01 \
  --tts indextts --speaker-wav "<path to reference voice wav>" \
  --overwrite-audio --overwrite-video \
  --build-long-video --normalize-audio \
  --background-music "<path to music>" --music-volume-db -28

# Kokoro fallback (fast, ~4x parallel on an RTX 3060 — do not exceed 4 gpu-workers):
mediaconductor video --project-root library/<Project> --items 01 \
  --tts kokoro --gpu-workers 4 \
  --build-long-video --normalize-audio \
  --background-music "<path to music>" --music-volume-db -28
```

- Use the **default audio/output roots** (don't pass `--audio-root
  audio/<Project>` — the project name is appended automatically and you
  get a doubled path).
- Production defaults to `--audio-source faded`: every panel WAV is copied to
  the separate `audio_faded/<Project>/...` tree with a symmetric 8 ms fade-in
  and fade-out before rendering. Raw IndexTTS/Kokoro WAVs in `audio/` stay
  untouched. `--audio-source raw` is for an intentional diagnostic comparison,
  not a normal production render.
- `--music-volume-db -28` (the default) is the tuned recap-channel value
  for this mixing chain: with the bed conditioned, EQ-carved, and ducked
  (all default-on) plus the 1.2 narration lift, −28 keeps the bed felt but
  never competing, and stays comfortable over a full long-form watch instead
  of fatiguing the listener (−15 is the masking boundary on phone speakers,
  −32 the inaudibility boundary under this chain). A punchier or sparser edit
  can move back up to −26 to −22. The music stem is loudness-aligned
  to the measured narration after its configured gain before the offset
  (`[music-loudnorm]` log line), so the value is a true LU separation whatever
  the track's mastering. The complete voice-plus-music mix is then normalized
  once, in two passes, to −14 LUFS / −1.5 dBTP; `--no-music-loudnorm`
  restores the old raw-offset behavior. An earlier production used −17
  before the loudnorm existed — with a hot-mastered YouTube-rip bed that
  was effectively ~−16 LU, slightly hot; a later production found even −26
  fatiguing over a full-length watch and moved the default down to −28.
- **The bed is conditioned + ducked automatically (all default-on).** Beyond
  the loudness offset, `video-add-bgm` now (a) compresses the music's own
  dynamic range so it sits at a *constant* level instead of swelling and
  receding on its own — a raw track's 6–10 LU loudness range is the top
  reason a bed sounds "unmixed" (the Thapin bed went from LRA 7.9 → 3.4);
  (b) dips the music gently in the 2–5 kHz vocal band so it masks the voice
  less; and (c) sidechain-ducks it a few dB under the narration so it
  breathes up in the gaps. Log lines to check: `[music-condition]` and
  `[music-loudnorm]`. Escape hatches if a track needs the raw treatment:
  `--no-condition-bed`, `--no-eq-carve`, `--no-duck`. Keep the duck ratio
  low for recaps (default 2) — wall-to-wall narration + a high ratio just
  makes the music uniformly quiet instead of dipping.
- **Music QC is automatic** — `video-add-bgm` scans the track's 20 ms RMS
  envelope before mixing (`mediaconductor/video_pipeline/music_bed.py`): splice
  holes (brief 25+ dB collapses mid-phrase — `silencedetect` can't see
  them) are cut out with short crossfades, silent lead/tail is trimmed,
  and when the track is defective or shorter than the video it's replaced
  by a crossfade-looped seamless bed, cached under `<work-dir>/music_bed/`
  and logged as a `[music-bed] ...` line. Check that line in the build
  log: `repaired N splice hole(s)` on a track you expected to be clean
  means the source file is damaged (common with YouTube-ripped WAVs —
  the 2026-07-06 incident shipped audible music cut-outs at 1:24 and 2:15
  of a published video before this existed). `--raw-music` bypasses the
  whole mechanism when you really want the file untouched. Re-mixing is
  still cheap: run `video-add-bgm` against the archived pre-BGM long video in
  `old/run_NNNN/`, then run `video-normalize-audio --input <mixed-file>
  --replace --target-i -14 --target-tp -1.5`. No re-render is needed and the
  duration (hence chapter timestamps) stays identical, but any BGM change
  invalidates the previous normalization.
- A published bad take can be replaced without a Studio trip. The safe default
  is upload the fixed file first, verify it, then delete the old id. Delete
  first only when the user explicitly requests that irreversible sequence;
  follow the exact replacement checklist in Phase 11.
- Old takes are archived to `old/run_NNNN/`, never destroyed.
- **After changing panels, narration or audio, pass `--overwrite-video`.**
  The renderer now also detects stale item videos by input mtimes and
  re-renders them ("inputs changed since last render"), but be explicit —
  a silent skip-if-exists once joined six outdated chapters into a
  "successful" build that was caught only by validate's duration check.
- **Chapter genuinely missing from the source** (a scanlation gap — a
  chapter that simply isn't on MangaDex): the join is strict and stops with
  `Missing item videos: NN`, which is what catches a silently-dropped render.
  When the chapter really doesn't exist, add `--allow-gaps` (on `video`, or on
  `video-join` if joining separately) — it stitches the chapters that exist, in
  order, skips the hole with a `[long-video] --allow-gaps:` log line, and lets
  the batch ship (e.g. ship 01, 03–12 as "chapters 1–12"; bridge the gap in the
  first narration line of the chapter after it). Don't use it to mask a failed
  render — re-render that chapter instead.
- Run it in the background and **wait for the completion notification**;
  IndexTTS for ~100 panels is a long job (see "Working style" above — don't
  sit in a poll loop). If audio state is ever in doubt:
  `mediaconductor video-audio-audit --project-root library/<Project> --json`.

## Phase 7 — Verify the build (measure, don't assume)

```bash
mediaconductor video-validate --project-root library/<Project> --items 01 --json
```

This command is a structural gate, not a visual, timing, click, or loudness
approval. Deliberately-unnarrated panels and orphan audio surface as `warnings`
(exit 0); anything in `errors` is real breakage — missing panels/audio for
*referenced* entries, duration mismatches (the item-WAV expectation is
frame-aligned; pass `--fps` if you rendered at a non-default rate), stream
problems.

Then verify the actual MP4:

- `ffprobe` duration/streams (expect 1920×1080, h264 + aac).
- Extract frames near the start / middle / end (`ffmpeg -ss <t> -i <mp4>
  -frames:v 1 out.png`) and **look at them**.
- Inspect narration-to-panel timing and listen across representative panel
  boundaries. Audit the first/last samples of the faded WAV derivatives; a
  structural `video-validate` pass cannot detect an edge click.
- Measure loudness: `ffmpeg -i <mp4> -map 0:a -af ebur128=peak=true -f null -`
  → integrated must be ≈ **−14 LUFS** and true peak no higher than
  **−1.5 dBTP**. If it comes out ~−20,
  something reintroduced the amix attenuation bug (see CLAUDE.md,
  "normalize=0") — YouTube never boosts quiet uploads.

## Phase 8 — Chapter timestamps (exact, not guessed)

Each panel is on screen for `ceil(wav_seconds × fps) / fps` (fps = 15,
`frame_aligned_duration()` in `mediaconductor/video_pipeline/item_assets.py`),
with no gaps. So cumulative WAV durations give frame-exact chapter marks:

```python
import json, math, wave
from pathlib import Path
FPS, t = 15, 0.0
entries = json.loads(Path("library/<Project>/01/narration.json").read_text("utf-8"))
for i, e in enumerate(entries):
    with wave.open(f"audio/<Project>/01/{Path(e['image']).stem}.wav") as w:
        dur = w.getnframes() / w.getframerate()
    print(i, f"{int(t)//60}:{int(t)%60:02d}", e["image"])
    t += max(1, math.ceil(dur * FPS)) / FPS
print("TOTAL", t)  # must equal the video duration — if not, timestamps are wrong
```

Pick the first entry of each act as a chapter. YouTube needs ≥3 chapters,
first at `0:00`, each ≥10 s. **Recompute after every audio regeneration**
— a different TTS voice shifts every boundary.

## Phase 9 — Thumbnail (1280×720)

Two proven approaches — pick by what tools are installed:

**A. Generated scene (high-energy recap key art).** Use one coherent
anime/manhwa illustration with a strong focal character, readable emotion,
clear silhouette separation, and enough negative space for the title. Keep it
platform-safe and faithful to the chapter; never sexualize young-looking
characters or rely on misleading imagery. Write the prompt for the actual
characters and scene rather than reusing a generic template, then generate it
with **Z-Image Turbo**:

```bash
mediaconductor zimage --prompt-file thumb_prompt.txt --output thumb_base.png \
    --width 1280 --height 720 --count 4   # generate 4 variants, pick the best
```

Prompt-writing rules (non-negotiable — this is what keeps the channel
monetizable, not optional flavor):

- **Every character drawn as a visibly adult, fully-clothed** — form-fitting
  or revealing-but-not-explicit outfits (the MamoruManhwa reference range:
  swimsuits, battle armor, low necklines) are the ceiling; no nudity, no
  transparent/see-through clothing, no explicit sexual content or pose, no
  characters that read as minors regardless of the source material's art.
- Composition: two- or three-character face-off, foreground face ~30% of
  frame height, depth-of-field background matching the story's actual
  setting, one side shocked/flustered/blushing, the other calm/smug/
  powered-up — the emotional contrast between the two *is* the hook.
- Glossy anime/manhwa rendering, saturated blues and gold, dramatic
  low-angle or dutch-tilt camera. No in-image text/logo/watermark — that
  gets added after, with `mediaconductor thumbnail-compose` (below), where it
  can be positioned precisely.
- Example prompt shape: `"glossy anime key art, [character A] blushing
  deeply with sparkling wide eyes and a flustered expression, form-fitting
  [outfit from the story], next to [character B] standing calm and
  confident with a faint smirk, [story setting] background softly blurred,
  saturated cyan-blue sky, dramatic low-angle shot, highly detailed,
  cinematic anime lighting, no text"`.

Then add the signature text/furniture with `mediaconductor thumbnail-compose`
(quick mode: repeated `--text` flags; full placement control via `--spec`
JSON — blocks/arrows/border; a custom PIL script is only needed for effects
beyond it, e.g. speech-tails and radial glows):
1–3 blocks of 1–4 words each — ALL-CAPS role labels + lowercase dialogue
quips — **yellow #FFE600 or white fills, black stroke ≈ 12% of font size**.
**Make the markup read hand-placed, not programmatic** (viewer feedback on a
shipped thumbnail: good art, but flat horizontal text + a thin line arrow
felt unnatural next to the reference channels):

- tilt the big hook block a few degrees (`"rotate": -3` … `-5`); keep small
  corner tags straight;
- arrows are **fat outlined block-arrows** (the default `"style": "block"`,
  width ≈ 22–30) pointing at a character/object, not thin lines;
- the built-in drop shadow (default on) separates text from busy art —
  don't disable it on detailed backgrounds;
- text may contain `\n` for stacked lines sharing one rotation.

Example spec:

```json
{"blocks": [
   {"text": "HE ATE\nHER SON?!", "x": 24, "y": 500, "size": 84, "rotate": -4},
   {"text": "TIGER MOM", "x": 556, "y": 22, "size": 62},
   {"text": "CH 1-7", "x": 28, "y": 22, "size": 44, "fill": "#FFFFFF"}],
 "arrows": [{"from": [742, 108], "to": [818, 178], "width": 26}],
 "border": true}
```

A live video's thumbnail can be replaced without re-uploading:
`mediaconductor youtube-thumbnail --profile <profile> --video-id <id>
--image <png>`.

**B. Panel collage (works with no image model).** Dramatic panel as
background, scaled to width, blurred (GaussianBlur ~2.5), darkened
(brightness ~0.42), warm/red tint blended through an elliptical mask;
subject panel cropped tight and pasted right at ~700 px tall with a 6 px
white sticker border; 3–5 words of text on the left in Impact, 90–120 pt,
white/yellow/red fills, black stroke (`stroke_width ≈ size//9`) plus a
small drop shadow.

Mandatory checks, all from real failures:

1. **Render it and look at it.** Never ship a thumbnail you haven't seen.
2. **Check every visible speech bubble in the crop** — a cut-off bubble
   can leave exactly the wrong words readable (the reference production's
   first thumbnail showed a truncated explicit line). Adjust the crop to
   exclude unsafe bubbles; a safe intriguing bubble is a bonus, not a risk.
3. Generated scenes: check hands/faces for AI artifacts before shipping;
   regenerate with a different seed rather than shipping a warped face.
4. Generated scenes: **look at all 4 variants against the prompt-writing
   rules above before picking one** — nothing nude, transparent, explicit,
   or minor-coded. Reject and regenerate with a tweaked prompt/seed rather
   than cropping around a borderline result; a thumbnail strike risks the
   whole channel.

## Phase 10 — Title, description, tags

- **Title** ≤ 100 chars. Two archetypes (big recap channels run both):
  - *Curiosity-gap premise* (browse/suggested traffic — the viral engine):
    `[He/She] + [unfair disadvantage] + BUT/AND + [OP payoff]! - Manhwa
    Recap`. 1–3 ALL-CAPS power words (SECRET, WORST, OP), concrete numbers
    ("9,999 times", "#1"), and — counterintuitively — **don't name the
    series**: "what's this called?" becomes the top comment and drives
    engagement. Put the series name in the description instead (and pin a
    comment naming it after upload).
  - *Search-intent* (evergreen): `<Series> Chapter X–Y Full Recap` /
    "...Full Story Recap in 30 Minutes". Use for catch-up mega-recaps.
- **Description** (write to a UTF-8 file): first ~150 chars are the search
  snippet — lead with the hook AND the main keyword ("<series> manhwa
  recap"); then a short story tease, `CHAPTERS` block from Phase 8, a
  binary comment-bait question (power-scaling debates are the
  highest-engagement format) + subscribe line, official-release credit
  (author + publisher, "support the official release"), a
  fair-use/transformative disclaimer, and **3–5 hashtags** (more dilutes;
  15+ and YouTube ignores all of them).
- **Tags**: comma-separated, ≤ 500 chars total — series name, genre
  phrases, "manhwa recap"/"manga recap", character names, "new manga
  <year>".

## Phase 11 — Upload (and replacing a bad take)

```bash
# Offline discovery: select the exact cached channel, never guess the profile:
mediaconductor youtube-profiles --json
mediaconductor youtube-status --profile <profile> --verify --json

mediaconductor youtube-upload --profile <profile> \
  --video output/<Project>/<Project>_full_<timestamp>.mp4 \
  --title "<title>" --description-file description.txt \
  --tags "tag1,tag2,..." --thumbnail thumbnail.png \
  --privacy public --json
```

- **Select and verify an explicit profile first.** `youtube-profiles --json`
  is offline and exposes no secrets; it reports the shared Desktop-app client
  path plus each cached channel. Ask the user when the intended channel is
  ambiguous. Pass the same `--profile` to every operation, including
  `default` when that is truly intended.
- With the shared client JSON present, live status/upload automatically opens
  browser consent for a missing, expired, revoked, or API-rejected grant and
  retries once after the channel owner approves. The agent initiates the call
  and waits; it never reads credentials. Use `--no-auto-auth` only for a
  pre-authorized headless worker.

- **Upload with `--privacy public`** — the channel owner's standing
  instruction is to publish directly, not leave the video private for a
  manual Studio step. Check the `--json` result's `privacy` field.
  Caveat: YouTube force-locks uploads from *unaudited* personal API
  projects to "Private (locked)" regardless of the requested privacy. If
  the result comes back private/locked despite `public`, stop and tell
  the human (the fix is completing YouTube's API audit for the Google
  Cloud project — not re-uploading). ~1,600 quota units of the
  10,000/day either way.
- Custom thumbnails need an eligible YouTube account. An authorization failure
  triggers the same browser reauthorization and one retry; a remaining failure
  is reported as `[warn] thumbnail not set ...` after the video upload succeeds.
- **Replacing a take:** the safe default is upload new → verify → delete old.
  Deletion-first creates immediate downtime and is allowed only when the user
  explicitly asks for that order. For either sequence:

  1. Run `youtube-status --profile <profile> --verify --json`, then
     `youtube-list --profile <profile> --json`; confirm the exact channel
     title/id and old video id/title.
  2. If deletion-first was explicitly requested, preview
     `youtube-delete --profile <profile> --video-id <old-id>`, then repeat
     with `--confirm --json` and verify the old id is absent.
  3. Upload the corrected, fully normalized file using that same explicit
     profile and verify the returned channel id, video id, URL, and privacy.
     In the safe default order, delete the old id only after this verification.
  4. Replace the matching `series-mark-published` record (including profile,
     channel id, and replaced video id when supported), then confirm both
     `youtube-list` and `series-plan --json` show the replacement.

  Deletion needs the full-management token (re-consent the same profile if it
  returns insufficient scopes). Never handle the stored bearer token. Update
  the description's chapter timestamps before re-uploading — a new voice can
  change them.

## Final checklist

- [ ] Every overlay sheet visually verified; bad pages overridden and re-cropped
- [ ] Whole chapter actually read; unsafe panels listed and excluded
- [ ] Hook = 4-ish late-chapter shock panels as renamed copies; CTA outro present
- [ ] `mediaconductor video-check --json` ok before building
- [ ] Faded per-panel derivatives audited; raw TTS unchanged; no edge clicks
- [ ] Final MP4: duration/timing sane, frames spot-checked, ≈ −14 LUFS and ≤ −1.5 dBTP
- [ ] Timestamps recomputed from the *current* WAVs; total matches duration
- [ ] Thumbnail rendered, viewed, no unsafe bubble text; if generated with
      Z-Image, all variants checked against the prompt-writing safety rules
- [ ] Title ≤ 100 chars, tags ≤ 500 chars, description leads with the hook
- [ ] Uploaded with `--privacy public` + thumbnail set; profile/channel/id/privacy verified
- [ ] Replacement publish record written and YouTube list + series plan rechecked
