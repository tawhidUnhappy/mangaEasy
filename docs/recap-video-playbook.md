# Manga recap video playbook — for AI agents

This is the exact, end-to-end recipe used to produce a full YouTube manga
recap video autonomously with mangaEasy (reference production: *Irozuku
Monochrome* ch. 1 — 9:05, 96 narrated panels, IndexTTS voice clone,
uploaded with thumbnail/title/description/chapters). Follow it top to
bottom. Everything here was learned the hard way in a real production;
the **bold warnings are the places it actually went wrong**.

Read `docs/ai-guide.md` (CLI contract) and the repo `CLAUDE.md` first.
All commands run from the install root (`uv run mangaeasy ...` in a dev
checkout).

---

## Phase 0 — Environment

```bash
mangaeasy where --json      # resolved paths; run this first
mangaeasy doctor --json     # ffmpeg/GPU/tool status
mangaeasy tools --json      # which external tool envs are installed
```

Install what's missing:

```bash
mangaeasy install-tool magi-v3     # panel detection (needed for paged manga)
mangaeasy install-tool index-tts   # optional: voice-clone TTS (GPU, slow, best quality)
# Kokoro installs the same way if absent: mangaeasy install-tool kokoro-82m
```

YouTube must be connected once (browser consent — the human does this):
`mangaeasy youtube-auth`, verify with `mangaeasy youtube-status --verify`.
See `docs/youtube.md` for the one-time Google Cloud setup and what the
token can/can't do.

## Phase 1 — Download the chapter

Set the `download` block of `config.json` (project root): MangaDex title
URL, chapter number, `translated_language`. Then:

```bash
mangaeasy download
```

Put/keep the raw pages in `library/<Project>/<item>/download/` (item =
zero-padded chapter, e.g. `01`). Page files are `01_00.jpg … 01_NN.jpg`.

`download` also writes/updates `library/<Project>/manga.json` — the manga's
source record (MangaDex title URL, canonical title, per-chapter download
info). Read it later when you need the manga's link or the official title,
e.g. for the description's credits / "support the official release" section
(`mangaeasy library-list --json` includes it as each project's `manga`
field).

## Phase 2 — Panel detection (MAGI v3)

**This applies to paged manga.** Vertical webtoons don't need MAGI — use
`mangaeasy gutter-split` or the `mangaeasy cut-page` editor instead.

The repo ships a single-image adapter
(`mangaeasy/assets/tools/detect_magi.py`, copied into the tool env by
`install-tool`), but it reloads the model per call. For a whole chapter,
load the model **once** and loop. Find the tool env via
`mangaeasy tools --json`, then run this with the env's own python
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
(`_manga_reading_order()` in `mangaeasy/panels/ai.py` — RTL band-overlap
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

def reading_order(boxes):  # mirrors mangaeasy/panels/ai.py
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

## Phase 5 — Write `narration.json`

Format (`library/<Project>/<item>/narration.json`):

```json
[{"image": "01_04_01.png", "narration": "One sentence or three. Present tense."}]
```

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

- **Audio is keyed by image stem.** If the hook reuses a story panel, the
  two entries would share one WAV. Make renamed copies for the hook
  (`01_00_01.png`, `01_00_02.png`, …) and reference those.
- Skip the unsafe panels from Phase 4 entirely; keep plot-critical
  borderline panels brief and frame them as comedy/panic, never salacious.
- Style: present tense, short punchy sentences, escalation words,
  callbacks to earlier lines, name the antagonist. One entry ≈ 2
  sentences ≈ 5–7 s of TTS.

Validate inputs before burning GPU time:

```bash
mangaeasy video-check --project-root library/<Project> --items 01 --json
```

## Phase 6 — Build the video

One command runs audio → render → join → normalize → BGM:

```bash
# Kokoro (fast, ~4x parallel on an RTX 3060 — do not exceed 4 gpu-workers):
mangaeasy video --project-root library/<Project> --items 01 \
  --tts kokoro --gpu-workers 4 \
  --build-long-video --normalize-audio \
  --background-music "<path to music>" --music-volume-db -17

# IndexTTS voice clone (much slower, best quality; leave gpu-workers at default):
mangaeasy video --project-root library/<Project> --items 01 \
  --tts indextts --speaker-wav "<path to reference voice wav>" \
  --overwrite-audio --overwrite-video \
  --build-long-video --normalize-audio \
  --background-music "<path to music>" --music-volume-db -17
```

- Use the **default audio/output roots** (don't pass `--audio-root
  audio/<Project>` — the project name is appended automatically and you
  get a doubled path).
- `--music-volume-db -17` is the recap-channel sweet spot (music audible,
  voice clearly on top; the value is dB below the narration, which is
  never attenuated). −15 to −20 is the sane range.
- Old takes are archived to `old/run_NNNN/`, never destroyed.
- Run it in the background and poll/wait; IndexTTS for ~100 panels is a
  long job. If audio state is ever in doubt:
  `mangaeasy video-audio-audit --project-root library/<Project> --json`.

## Phase 7 — Verify the build (measure, don't assume)

```bash
mangaeasy video-validate --project-root library/<Project> --items 01 --json
```

(When you deliberately narrate a subset of panels, the "narration and
panel names do not match" error is expected — check the counts instead:
narration == panel_audio, item_videos == 1.)

Then verify the actual MP4:

- `ffprobe` duration/streams (expect 1920×1080, h264 + aac).
- Extract frames near the start / middle / end (`ffmpeg -ss <t> -i <mp4>
  -frames:v 1 out.png`) and **look at them**.
- Measure loudness: `ffmpeg -i <mp4> -map 0:a -af ebur128=peak=true -f null -`
  → integrated must be ≈ **−14 to −13.5 LUFS**. If it comes out ~−20,
  something reintroduced the amix attenuation bug (see CLAUDE.md,
  "normalize=0") — YouTube never boosts quiet uploads.

## Phase 8 — Chapter timestamps (exact, not guessed)

Each panel is on screen for `ceil(wav_seconds × fps) / fps` (fps = 15,
`frame_aligned_duration()` in `mangaeasy/video_pipeline/item_assets.py`),
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

Composition that worked (PIL): dramatic panel as background, scaled to
width, blurred (GaussianBlur ~2.5), darkened (brightness ~0.42), warm/red
tint blended through an elliptical mask; subject panel cropped tight and
pasted right at ~700 px tall with a 6 px white sticker border; 3–5 words
of text on the left in Impact, 90–120 pt, white/yellow/red fills, black
stroke (`stroke_width ≈ size//9`) plus a small drop shadow.

Two mandatory checks, both from real failures:

1. **Render it and look at it.** Never ship a thumbnail you haven't seen.
2. **Check every visible speech bubble in the crop** — a cut-off bubble
   can leave exactly the wrong words readable (the reference production's
   first thumbnail showed a truncated explicit line). Adjust the crop to
   exclude unsafe bubbles; a safe intriguing bubble is a bonus, not a risk.

## Phase 10 — Title, description, tags

- **Title** ≤ 100 chars: curiosity gap + contradiction, then
  `| <Series> Ch. N Recap`. Pattern: "He Tried To Propose To A Nun... And
  Freed A Demon King Instead | Irozuku Monochrome Ch. 1 Recap".
- **Description** (write to a UTF-8 file): first ~150 chars are the search
  snippet — make them the hook; then a short story tease, `CHAPTERS`
  block from Phase 8, a binary comment-bait question + subscribe line,
  official-release credit (author + publisher, "support the official
  release"), a fair-use/transformative disclaimer, and 5–7 hashtags.
- **Tags**: comma-separated, ≤ 500 chars total — series name, genre
  phrases, "manga recap", "new manga <year>".

## Phase 11 — Upload (and replacing a bad take)

```bash
mangaeasy youtube-upload \
  --video output/<Project>/<Project>_full_<timestamp>.mp4 \
  --title "<title>" --description-file description.txt \
  --tags "tag1,tag2,..." --thumbnail thumbnail.png \
  --privacy public --json
```

- **Upload with `--privacy public`** — the channel owner's standing
  instruction is to publish directly, not leave the video private for a
  manual Studio step. Check the `--json` result's `privacy` field.
  Caveat: YouTube force-locks uploads from *unaudited* personal API
  projects to "Private (locked)" regardless of the requested privacy. If
  the result comes back private/locked despite `public`, stop and tell
  the human (the fix is completing YouTube's API audit for the Google
  Cloud project — not re-uploading). ~1,600 quota units of the
  10,000/day either way.
- Custom thumbnails need a phone-verified YouTube account. The upload
  prints `[warn] thumbnail not set: ...` on failure and nothing on success.
- **Replacing a take**: upload the new video first, verify the `--json`
  result, *then* delete the old one — never the reverse. Deletion needs
  the full-management token (see docs/youtube.md; re-run
  `mangaeasy youtube-auth` if a delete returns 403) and is a raw API call
  (`DELETE https://www.googleapis.com/youtube/v3/videos?id=<id>` with the
  stored bearer token) or one click in Studio. Update the description's
  chapter timestamps *before* re-uploading — a new voice changes them.

## Final checklist

- [ ] Every overlay sheet visually verified; bad pages overridden and re-cropped
- [ ] Whole chapter actually read; unsafe panels listed and excluded
- [ ] Hook = 4-ish late-chapter shock panels as renamed copies; CTA outro present
- [ ] `mangaeasy video-check --json` ok before building
- [ ] Final MP4: duration sane, frames spot-checked, integrated ≈ −14 LUFS
- [ ] Timestamps recomputed from the *current* WAVs; total matches duration
- [ ] Thumbnail rendered, viewed, no unsafe bubble text
- [ ] Title ≤ 100 chars, tags ≤ 500 chars, description leads with the hook
- [ ] Uploaded with `--privacy public` + thumbnail set; `--json` result's privacy verified (and human told what to delete, if replacing)
