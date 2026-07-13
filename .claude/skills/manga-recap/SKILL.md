---
name: manga-recap
description: >
  Produce narrated manga/webtoon recap videos for YouTube with the mangaeasy
  CLI: download a series from a MangaDex URL, crop panels (webtoon or paged),
  verify crops, write and verify narration, generate TTS audio, render and
  join videos, mix background music, generate a thumbnail, and upload — in
  12-chapter batches. Use when the user gives a MangaDex URL, asks for a
  manga/manhwa/webtoon recap video, or asks to continue/publish the next
  batch of an existing recap series.
---

# Manga recap production (mangaEasy)

You drive the whole pipeline through the `mangaeasy` CLI (or its MCP tools —
same engine). Full reference: `docs/ai-guide.md`. Machine contract: every
`--json` command prints one JSON object; generation commands end with a
`MANGAEASY_RESULT {...}` line; exit 0 = ok, 1 = failure, 2 = usage error;
nothing ever prompts for input.

**Hard safety rules** — never delete/rename anything inside `library/`
source items; never touch `narration.backup.json`; clear generated output
only with `video-clean-*`; keep `--gpu-workers` ≤ 4.

**Run long steps in the background, then wait — don't burn compute polling.**
`download`, `page-split`/`webtoon-split`, `panel-transcript`, `video`,
`zimage`, and `youtube-upload` each run for minutes to tens of minutes. Launch
each as a background job and stop; let the harness's completion notification
wake you instead of sleeping or re-checking in a loop. GPU tools (MAGI,
DeepSeek-OCR, IndexTTS, Z-Image) block-buffer stdout, so their logs look empty
until the end — judge health from filesystem signals (growing panel/transcript
counts, output files appearing, `nvidia-smi`), not by tailing the log. Only
foreground the quick `--json`/validation commands.

## 0. Orient (every session)

```bash
mangaeasy where --json      # install paths + version
mangaeasy doctor --json     # ffmpeg/GPU/tool readiness
mangaeasy work-status --project-root library/<Project> --json   # resuming? exact per-item stage
```

Resuming a project, or working alongside other agents? Follow
`docs/multi-agent.md`: `work-status --next` names the unclaimed actionable
tasks, `work-claim` leases an item+stage (and `--resource gpu` serializes the
GPU model tools), `work-note` shares character names/speaker conventions
between narrators, and `mangaeasy work-qa` is the fix-until-clean gate — loop
`work-qa → apply the listed fix → work-qa` until exit 0. `work-artifacts`
lists what already exists for reuse before you regenerate anything.

Fresh clone/machine? Follow the agent runbook in `docs/setup.md`:
`uv sync` → `mangaeasy setup` (GPU-aware; `--all` / `--minimal` /
`--skip <tool>`; re-run to resume) → verify `doctor --json` → `mangaeasy
smoke-test` (renders and checks a tiny real video; `SMOKE TEST PASS` = the
machine can produce videos). Working dir for a production run should be the
install root — projects live in `library/`, generated output in `audio/`,
`output/`, `work/`.

## 1. Download the series (user gives a MangaDex URL)

```bash
mangaeasy download --url "<mangadex title url>" --all
```

Polite by design (rate spacing, backoff, jitter) — never parallelize
downloads or shrink its delays. `--name <Project>` overrides the derived
folder name; `--from/--to` bound the range. Re-running resumes; complete
chapters are skipped. The result line gives the project path
(`library/<Project>/`), and `manga.json` records the source.

## 2. Plan the batch

Videos ship 12 chapters at a time (01–12, then 13–24, …):

```bash
mangaeasy series-plan --project-root library/<Project> --json
```

Work on `next_batch` only. If it's partial, the series may have ended
(fine — ship what exists) or later chapters aren't downloaded yet.

## 3. Decide the crop tool, then crop and VERIFY

```bash
mangaeasy style-detect --project-root library/<Project> --json
```

Open 2–3 of the returned `sample_images` and confirm the verdict yourself:
endless vertical strips → `webtoon-split`; discrete pages with panel grids →
`page-split` (needs `install-tool magi-v3`). Then crop the batch, e.g.:

```bash
mangaeasy webtoon-split --project-root library/<Project> --item-range 01-12
```

**The crop double-verify loop** (details: `docs/operate/crop-verify-narrate.md`):
the result lists per-item `suspects` / `content_drops` and the exact
`verify_images`. For webtoons, then run the full-resolution pass — judging
crops on downscaled sheets alone has shipped sliced bubbles before:

```bash
mangaeasy webtoon-cutcheck --project-root library/<Project> --item-range 01-12
```

Read EVERY sheet it writes; FIX any cut through a figure/speech bubble and
any bubble/SFX-fragment short panel by adding the fix with `webtoon-override`
(never compute merge indices by hand — it resolves them from the manifest):

```bash
mangaeasy webtoon-override --file work/overrides.json \
    --project-root library/<Project> --item 07 --merge-at-cut 23140
# fuse sheet panels #4..#5:            --item 12 --merge-panels 4,5
# reposition a bad cut:                --merge-at-cut 42186 --split-at 42394
```

ACCEPT background/effect-art cuts, bordered thin scenery, scanlator
banners. Re-run the split with `--overrides work/overrides.json`, then
re-run cutcheck to confirm. Do not proceed to narration with unresolved
suspects.

**Re-cropping after narration exists?** Never re-narrate: `mangaeasy
panels-remap --project-root library/<Project> --item-range 01-12` (dry run,
then `--apply`) carries narration texts and WAVs to the new numbering, then
review its `shift`/`merge` list with `narration-review-sheets
--only-images ...` and rebuild with `mangaeasy video --overwrite-video`.

## 4. Write narration grounded in transcripts, then verify it

First OCR every panel (needs `install-tool deepseek-ocr2`):

```bash
mangaeasy panel-transcript --project-root library/<Project> --item-range 01-12
```

Then write `library/<Project>/<item>/narration.json`
(`[{"image": "<panel file>", "narration": "..."}]`) from **panel image +
transcript together** — style rules in
`mangaeasy/assets/prompts/narration.md`. Optional `intro.json` (same shape)
gives chapter 01 a cold-open hook reel — it is **prepended** before that
chapter's `narration.json`, so its panels must be ones the chapter's
`narration.json` does **not** also use, or they play twice (the cold-open
replays a beat, then it shows again in-context — a viewer-reported "why is the
start repeating?"). Either give the intro its own distinct panels, or drop
those panels from `narration.json`; `narration-check` now fails on the overlap.
Grounding rules (each traces to real viewer complaints about a shipped recap):

- **one beat per panel** — the line describes THAT panel, never a summary of
  several panels smeared over one image;
- **paraphrase anchored to the transcript** — reword freely, but the meaning
  must match the panel's actual bubble text;
- **speakers attributed from the panel** (who is on-panel, whose bubble
  tail) — if unsure, narrate without naming;
- **no punctuation-only lines** (`"?!"` → near-empty TTS audio; video-check
  flags these as unspeakable);
- **optional `"emotion"` field** on the few lines that earn it (reveals,
  battle cries, tearful goodbyes): a 1–3 word phrase like `"tense"` or
  `"cold, menacing"` that IndexTTS2 blends into the voice — vocabulary and
  rules in `mangaeasy/assets/prompts/narration.md`; most lines stay neutral
  (no field).

Verify in two passes:

1. **Structural** — `mangaeasy narration-check --project-root
   library/<Project> --item-range 01-12 --json` must pass (`ok:true`): no
   dangling images, no empty text, no intro/narration overlap. Panels with no
   narration entry are reported as **warnings**, not failures — deliberately
   skipping credits/title banners, scanlator pages, SFX-only frames, and
   duplicate reaction beats is correct (the renderer builds the video **only**
   from narrated panels). Confirm the uncovered list is exactly those skips,
   not a story beat you forgot.
2. **Semantic** — `mangaeasy narration-review-sheets --project-root
   library/<Project> --item-range 01-12`, then Read EVERY sheet (panel +
   narration + OCR side by side) and check the grounding rules above.
   Fix each bad line with one command (stale WAV pruned automatically):
   `mangaeasy narration-edit --project-root library/<Project> --item 01
   --set <image> "<new line>" --prune-audio`. Use `--delete <image>`,
   `--list`, `--intro`, or `--set-json '[...]'` for bulk edits — no
   hand-editing of narration.json needed.

## 5. Audio → render → join → music

```bash
mangaeasy video --project-root library/<Project> --audio-root audio \
    --output-root output --item-range 01-12 --tts auto \
    --build-long-video --normalize-audio \
    --background-music "<music file>"
```

`--tts auto` uses IndexTTS (voice cloning) when an NVIDIA GPU + model +
speaker WAV are available, otherwise Kokoro. Music is mixed low under the
narration by design — conditioned, loudness-aligned, side-chain ducked at
`--music-volume-db` −22 dB default (keep within −18…−24; narration is
normalized to −14 LUFS first). **Rebuilding after any panel/narration/audio
change: pass `--overwrite-video`** (stale item videos are also mtime-detected
now, but be explicit — a silent skip once shipped six outdated chapters).
**Chapter genuinely missing from the source** (e.g. a scanlation gap — a
chapter that just isn't on MangaDex): the join is strict by design and stops
with `Missing item videos: NN`. Add **`--allow-gaps`** so it stitches the
chapters that exist, in order, and skips the hole with a warning — the story
still reads continuously; bridge the gap in the narration of the following
chapter's first line. (Don't reach for it to paper over a *failed render* —
re-render that chapter instead.)
After the run:
`mangaeasy video-validate --project-root library/<Project> ... --json` —
`warnings` (unnarrated panels, orphan audio) are informational; anything in
`errors` blocks upload.
Full recipe + troubleshooting: `docs/recap-video-playbook.md`.

## 6. Thumbnail (1280×720)

1. Generate key art (style rules and the platform-safe prompt shape are in
   `docs/thumbnail.md` — visibly adult, fully clothed, suggestive-ceiling,
   no text in the image):
   `mangaeasy zimage --prompt-file thumb_prompt.txt --output thumb.png
   --width 1280 --height 720 --count 4`
2. Open all variants, pick the best (faces and hands intact).
3. Add text furniture — 1–3 blocks, 3–5 punchy words each, highlighting one
   shocking fact from the batch:
   `mangaeasy thumbnail-compose --base thumb_03.png --output final_thumb.png
   --text "HE ATE A GOD?!" --text "CH 1-12"`
   (full control via `--spec` JSON: blocks/arrows/border). Make the markup
   read hand-placed: tilt the big hook block (`"rotate": -3…-5`), use the
   default fat outlined block-arrows (width ≈ 22–30) instead of thin lines,
   keep the drop shadow on.
4. **Open the final image at full size** and check text overlap, edges, and
   anything that could read as explicit — fix and re-compose if needed.
5. Iterating after upload? `mangaeasy youtube-thumbnail --video-id <id>
   --image final_thumb.png` swaps the live thumbnail without re-uploading.

## 7. Title, description, upload

Title ≤ 100 chars: hook + series name + chapter range, front-load the hook
(e.g. "He Ate a God and Leveled Up — <Series> Recap Chapters 1–12").
Description: 2–3 sentence spoiler-light hook, then chapter range, then
5–10 search phrases people actually type. Tags: comma-separated
series/genre terms.

```bash
mangaeasy youtube-upload --video output/<Project>/<Project>_full.mp4 \
    --title "..." --description "..." --tags "manga,recap,..." \
    --thumbnail final_thumb.png --privacy public --json
```

Needs a prior human `youtube-auth` (see `docs/youtube.md`). **Check the token
is live first: `mangaeasy youtube-status --verify --json`.** The stored OAuth
token expires/gets revoked silently, and a dead one only surfaces mid-upload as
`invalid_grant: Token has been expired or revoked` after the whole video was
built. If `verified` is false, the user must re-run `youtube-auth` (interactive
browser consent — an agent can't do it headless); everything else is already
done, so just retry the upload afterward. Default privacy is `private` and
unaudited API projects are force-locked to it — use `--privacy public` only
when the channel's API project supports it, and verify the JSON result says the
privacy you asked for. Then record the batch so the plan advances:

```bash
mangaeasy series-mark-published --project-root library/<Project> \
    --items 01-12 --video-id <id from upload> --title "..."
```

## 8. Next batch

Re-run `series-plan` — it now names the next window (13–24, …). Repeat from
step 3 (chapters are already downloaded). When all batches are published,
report the uploaded video URLs and stop.
