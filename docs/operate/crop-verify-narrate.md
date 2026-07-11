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

Alongside the crops it writes `work/webtoon_verify/<Project>/<item>_ranges.json`
— the **ranges manifest**: every final panel's `top`/`bottom` in stitched-strip
coordinates plus the `forced_cuts` list (auto-split cuts whose quiet band was
suspiciously energetic — the prime suspects for sliced bubbles).

Correct a bad item with `--overrides` (JSON keyed by item name):

```json
{"07": {"split_at": [23140]}, "12": {"merge": [[4, 5]]}}
```

- `merge [[i, j]]` indices are **0-based positions in the `final` list of a
  no-override run** (= panel number − 1; the manifest's own `index` field is
  1-based, crop file numbers are position + 1). Never derive them by eye —
  find the panel whose `top`/`bottom` matches the defect's y in the manifest
  and compute from its list position. Merges are applied in reverse-sorted
  order, so several against the same base run compose safely.
- `split_at` values are absolute stitched-strip y-coordinates, applied
  **after** merges. To reposition a bad cut: merge across it, then `split_at`
  the correct y. Pick that y from the pixel data (a blank-row run or a hard
  panel edge), not from a scaled screenshot — estimates routinely land on
  faces. A `(top, y)` fragment shorter than 20 px is dropped automatically,
  which is the clean way to shave a junk sliver off a repositioned cut.
- `replace` swaps an item's whole range list.

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

For webtoons, start with the **cutcheck pass** — it exists because judging
crops on downscaled contact sheets shipped a video with half panels, fused
panels and sliced speech bubbles that all had to be redone:

```bash
mangaeasy webtoon-cutcheck --project-root library/<Project> --item-range 01-07
```

It renders a full-resolution window (±650 px of context) around **every
forced cut and every short panel** from the ranges manifests, montaged into
review sheets under `work/cutcheck/<Project>/`. Read every sheet and give
each flagged location a verdict on the actual art:

- **FIX** (add a `merge`, or a merge + repositioned `split_at`): the cut
  passes through a figure or a speech bubble; a short panel is a bubble/SFX
  fragment whose art continues into a neighbour (merge it toward its
  bubble-mate — direction matters).
- **ACCEPT**: cuts through pure background or effect art, bordered thin
  scenery panels, scanlator promo banners (those get skipped in narration
  instead).

Collect all fixes into one overrides file, re-run `webtoon-split`, then re-run
`webtoon-cutcheck` and confirm the fixed locations are clean. If narration
already exists for the old crops, do **not** re-narrate — see
"Re-cropping after narration exists" below.

Then check the standard verification images, per page/strip:

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

### Re-cropping after narration exists (`panels-remap`)

Re-running a splitter renumbers every panel, orphaning `narration.json` and
the per-panel WAVs. Never re-narrate to fix that:

```bash
mangaeasy panels-remap --project-root library/<Project> --item-range 01-07   # dry run
mangaeasy panels-remap --project-root library/<Project> --item-range 01-07 --apply
```

It locates each archived old panel's span in the stitched strip, maps old →
new panels by interval overlap (survives merges, splits and shifted
boundaries), then carries narration texts verbatim and copies/concatenates
the WAVs into the new numbering. Hook/CTA physical copies are restored too.
Refuse to `--apply` while the dry run reports orphans or bad locates; pass
`--old-run` explicitly if the item was re-cropped more than once (the
narration must match that archive). Afterwards, review every `shift`/`merge`
panel with `narration-review-sheets --only-images ...` and rebuild with
`mangaeasy video --overwrite-video` (stale item videos are also auto-detected
now, but be explicit).

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

## Step 3.5 — Transcribe the bubbles first (`panel-transcript`)

```bash
mangaeasy install-tool deepseek-ocr2   # one-time
mangaeasy panel-transcript --project-root library/<Project> --item-range 01-07
```

This OCRs every panel into `<item>/transcript.json`. Write narration **from
panel image + transcript together** — it is the difference between narration
that lands and narration that reads wrong. Real viewer feedback on a shipped
recap: speakers misattributed, lines that summarized several panels smeared
over one, paraphrases that drifted from what the character actually said.
All three are symptoms of narrating from memory of a 500-panel read-through
instead of from the panel's actual text.

---

## Step 4 — Write `narration.json`

One object per panel you narrate, keyed by the crop's **filename**:

```json
[
  {"image": "01_004_01.jpg", "narration": "One to three present-tense sentences."}
]
```

**Per-panel grounding rules** (each one traces back to shipped-video
feedback):

- **One beat per panel.** The line describes what is visible in THAT panel
  only. Story summary belongs across consecutive panels' lines, never inside
  one panel's line while the viewer stares at a different image.
- **Anchor dialogue to the transcript.** Paraphrase freely for voice and
  pacing, but the meaning must match the OCR text of that panel's bubbles;
  when a paraphrase reads awkward, quoting the bubble (trimmed) is better.
- **Attribute speakers from the panel, not from memory.** Who is on-panel?
  Whose bubble is it (tail direction)? If the speaker isn't visible or
  certain, narrate the line without naming ("someone snarls from the
  crowd...") rather than guessing.
- **Say it aloud.** TTS reads exactly what you write — punctuation-only
  entries like `"?!"` produce a ~0.03 s WAV (`video-check` flags these as
  "unspeakable"); give reaction panels a real line.

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
actually matters: the JSON parses, no two entries share an image stem, every
referenced image exists on disk, and there are **no "unspeakable narration
text" warnings** (those become corrupt near-empty WAVs).

## Step 5 — Verify the narration semantically (`narration-review-sheets`)

`narration-check` proves structure; it deliberately does not prove the words
are right. That pass is:

```bash
mangaeasy narration-review-sheets --project-root library/<Project> --item-range 01-07
```

Each sheet pairs a panel image with the narration line that will be spoken
over it and the panel's OCR transcript. Read **every** sheet and check the
four grounding rules above (this-panel-only, dialogue matches OCR, speaker
right, reads naturally aloud). Fix by editing `narration.json`, delete the
affected WAVs, and re-run audio generation — it only regenerates missing
files. After a `panels-remap`, `--only-images` with the remap review list
narrows the pass to the panels that actually changed.

---

## Next

You now have verified `panels/` + `narration.json`. Continue with
[the recap-video playbook](../recap-video-playbook.md) from **Phase 6 — Build
the video** (audio → render → join → BGM → thumbnail → upload).

## Command reference

| Command | Role | Source |
|---|---|---|
| `webtoon-split` | crop vertical strips + verify sheets + ranges manifest | [panels/webtoon.py](../../mangaeasy/panels/webtoon.py) |
| `webtoon-cutcheck` | full-res review windows for every forced cut / short panel | [panels/cutcheck.py](../../mangaeasy/panels/cutcheck.py) |
| `panels-remap` | carry narration + audio across a re-crop | [panels/remap.py](../../mangaeasy/panels/remap.py) |
| `page-split` | crop paged manga (MAGI v3) + verify sheets | [panels/page.py](../../mangaeasy/panels/page.py) |
| `panel-transcript` | OCR every panel to ground narration/speakers | [ocr/panel_transcript.py](../../mangaeasy/ocr/panel_transcript.py) |
| `narration-review-sheets` | panel + narration + OCR sheets for semantic QA | [video_pipeline/narration_sheets.py](../../mangaeasy/video_pipeline/narration_sheets.py) |
| `video-check` | validate item inputs before building (incl. unspeakable text) | [video_pipeline/check_items.py](../../mangaeasy/video_pipeline/check_items.py) |
