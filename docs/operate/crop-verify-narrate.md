# Crop → verify → see → narrate

The core loop of mangaEasy: turn a folder of raw manga/webtoon pages into
verified panel crops, look at every one, and write the narration script that
drives the video. This is the **flagship operator doc** — self-contained, and
the template every other operator doc is written to. When you have crops +
`narration.json`, hand off to `docs/recap-video-playbook.md` (Phase 6 on) for
audio, video, thumbnail, and upload.

> **Golden rule of this whole loop:** *never trust a detector's boxes.* Both
> crop commands emit verification images specifically so you can catch the
> wrong ones before they poison the narration. Clearing every flag by eye is
> not optional — it is the point of this doc.

Prerequisites: a project laid out as `library/<Project>/<item>/download/`
(raw pages), one `item` per chapter (`01`, `02`, …). See
[the data layout](../../CLAUDE.md) ("Data layout") for the full folder contract.
Orient first with `mangaeasy where --json` and `mangaeasy doctor --json`.

---

## Step 0 — Decide: webtoon or page?

| Source shape | Use | Detector |
|---|---|---|
| **Vertical strip** — one endless scroll, panels separated by gutters (Korean/Chinese webtoons) | `mangaeasy webtoon-split` | gutter analysis (no GPU model) |
| **Paged manga** — discrete pages, multiple panels per page (Japanese tankōbon, most scanlations) | `mangaeasy page-split` | MAGI v3 (needs `install-tool magi-v3`) |

Let the machine measure first:

```bash
mangaeasy style-detect --project-root library/<Project> --json
```

It reports a per-item and overall verdict (`webtoon` / `paged` / `uncertain`)
from the page aspect ratios, plus `sample_images` to confirm visually — open
2–3 of them before committing to a splitter. Tall images many times taller
than wide → webtoon. Roughly page-shaped images with panel grids → paged.
`uncertain` or mixed verdicts mean look at the actual pages and decide.

---

## Step 1A — Crop a **webtoon** (`webtoon-split`)

```bash
mangaeasy webtoon-split --project-root library/<Project> --item-range 01-19
```

Per item it stitches `download/` into one tall strip, splits at gutters, then
applies two production fixups: **auto-split** (any panel taller than 2.2× width
is re-cut at the quietest row, so one missed gutter doesn't make a 10 000-px
panel) and **gap rescue** (dropped gutter-colored gaps that still contain
content — e.g. "ONE HOUR LATER…" captions — are attached to the next panel).

- Crops land in `library/<Project>/<item>/panels/ch<item>_###.jpg` (an existing
  `panels/` is archived to `<item>/old/run_NNNN/` first — never destroyed).
- Verify images land in `work/webtoon_verify/<Project>/`:
  - `NN_sheet_K.png` — numbered contact sheet; suspects get a red `!!`.
  - `NN_strip_K.png` — downscaled strip: **green** = kept panel, **blue** =
    auto-cut line, **red** = dropped rows.

Correct a bad item with `--overrides` (JSON keyed by item name):

```json
{"07": {"split_at": [23140]}, "12": {"merge": [[4, 5]]}}
```

`replace` swaps an item's whole range list; `merge` indices are 0-based
inclusive; `split_at` values are stitched-strip y-coordinates read off the
strip overlay.

Implementation: [mangaeasy/panels/webtoon.py](../../mangaeasy/panels/webtoon.py).

## Step 1B — Crop **paged manga** (`page-split`)

```bash
mangaeasy install-tool magi-v3        # one-time; downloads the model on first run
mangaeasy page-split --project-root library/<Project> --item-range 01-12 \
    --reading-direction rtl            # rtl = Japanese; ltr = Chinese/Korean
```

Per item it runs MAGI v3 **once** over every page in `download/`
(via the shipped `assets/tools/batch_detect_magi.py`, model loaded a single
time — not per page), sorts each page's boxes into manga reading order, and
crops them.

- Crops land in `library/<Project>/<item>/panels/<item>_<page>_<panel>.jpg`
  (e.g. `01_004_02.jpg`; page/panel are 1-based positions). Existing `panels/`
  is archived first, same as the webtoon path.
- Verify images land in `work/page_verify/<Project>/<item>/`:
  - `<item>_page_NNN.png` — the page with **numbered red boxes** in reading
    order. This is the primary thing to eyeball.
  - `<item>_sheet_K.png` — contact sheet of every crop.
  - `<item>_detections.json` — MAGI's raw boxes; read it to craft overrides.

**Suspect pages** are printed in the per-item report and are where MAGI most
often fails:

- `<page> no-panels` — MAGI found nothing, so the **whole page** became one
  crop. Usually a splash/spread page (fine) or a detection miss (fix it).
- `<page> full-page-box` — a single box covers most of the sheet; MAGI likely
  failed to split a multi-panel page.

Fix a page with `--overrides` (JSON keyed by item → page filename → pixel
boxes that **fully replace** MAGI's boxes for that page):

```json
{"01": {"01_09.jpg": [[0, 0, 900, 700], [0, 700, 900, 1400]]}}
```

Overlapping override boxes are fine and often correct — for a diagonal panel
border, overlap beats clipping a speech bubble. Re-run `page-split` for just
that item; it re-detects but your override wins for the listed pages.

Implementation: [mangaeasy/panels/page.py](../../mangaeasy/panels/page.py).
MAGI env pins live in [the magi-v3 notes](../../CLAUDE.md) and are baked into
`batch_detect_magi.py`.

---

## Step 2 — Verify **every** crop (the non-negotiable step)

Open the verification images and check, per page/strip:

1. **Coverage** — every panel has a box; nothing important is in a dropped
   (red) region. On webtoons, a red gap that still shows art/text is a miss —
   rescue it with an override.
2. **No merges/splits gone wrong** — no single box swallows two panels; no
   panel is chopped mid-art.
3. **Reading order** — the numbers follow manga order (right→left within a row
   then top→bottom for `rtl`; left→right for `ltr`), including across landscape
   spreads.
4. **No clipped bubbles** — a speech bubble cut at a box edge loses story text.

Every suspect / `content_drop` / `no-panels` / `full-page-box` flag must be
either (a) confirmed benign or (b) fixed with an override and re-cropped.
Known-benign patterns on real scanlations: a thin sliver at the top of a
webtoon = scanlator/credit banner; a trailing tall drop = "we're recruiting"
promo; bright mid-chapter slivers = SFX calligraphy. **Dialogue-bubble slices
flagged as suspects are real content — keep them.**

Wrong crops poison everything downstream: you would write narration against
images the viewer never sees correctly. Spend the time here.

---

## Step 3 — See the images and read the chapter

Before writing a single narration line, **read the whole chapter in order** —
the panel crops (`panels/`) or the raw pages. You are about to write ~100
beats; you can't hook a viewer on a story you skimmed. While reading, note:

- The 3–5 most shocking/funny panels — hook material for the cold open.
- Character names, the central irony, the cliffhanger.
- **Panels that are NOT platform-safe**: explicit dialogue in bubbles, risqué
  imagery, the credits/scanlator page. List them — they are excluded from
  narration and must never reach the thumbnail.

---

## Step 4 — Write `narration.json`

One object per panel you narrate, keyed by the crop's **filename**:

```json
[
  {"image": "01_004_01.jpg", "narration": "One to three present-tense sentences."}
]
```

Rules that hold across the pipeline:

- **`narration.json` lives at `library/<Project>/<item>/narration.json`.** The
  *only* reader is `video_pipeline/item_assets.load_narration()` — it also
  prepends `intro.json` if present (the cold-open mechanism). Never re-parse
  the file yourself.
- **Audio is keyed by image stem**, so two entries pointing at the same image
  would share one WAV. If the hook or CTA reuses a story panel, make a renamed
  **physical copy** and reference that (hook copies into a `_00_` page
  namespace; the CTA panel into a page number past the last real page).
- You normally narrate a **subset** of panels (hook/CTA copies + only
  story-carrying panels). That's expected — the pipeline renders exactly the
  panels named in `narration.json`, in order.
- **Style / structure**: cold-open hook (~25–30 s of the most absurd late
  panels as escalating questions, then "let's rewind") → acts (setup → inciting
  incident → escalation → climax, each ending on a mini-cliffhanger) → CTA
  outro on a striking panel. Present tense, short punchy sentences, name the
  antagonist, don't spoil ahead of the panel.

The full scriptwriting spec — speaker identification, speech types, tone — is
the narration prompt at
[mangaeasy/assets/prompts/narration.md](../../mangaeasy/assets/prompts/narration.md).
Feed it plus the panel images to an LLM, or write by hand.

Validate before spending GPU time:

```bash
mangaeasy video-check --project-root library/<Project> --items 01 --json
```

When you deliberately narrate a subset, `video-check` returns `"ok": false`
with "Narration count does not match panel count" — **that is expected**. What
actually matters: the JSON parses, no two entries share an image stem, and
every referenced image exists on disk.

---

## Next

You now have verified `panels/` + `narration.json`. Continue with
[the recap-video playbook](../recap-video-playbook.md) from **Phase 6 — Build
the video** (audio → render → join → BGM → thumbnail → upload).

## Command reference

| Command | Role | Source |
|---|---|---|
| `webtoon-split` | crop vertical strips + verify sheets | [panels/webtoon.py](../../mangaeasy/panels/webtoon.py) |
| `page-split` | crop paged manga (MAGI v3) + verify sheets | [panels/page.py](../../mangaeasy/panels/page.py) |
| `video-check` | validate item inputs before building | [video_pipeline/check_items.py](../../mangaeasy/video_pipeline/check_items.py) |
