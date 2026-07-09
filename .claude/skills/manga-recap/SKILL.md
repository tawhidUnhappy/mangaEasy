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

## 0. Orient (every session)

```bash
mangaeasy where --json      # install paths + version
mangaeasy doctor --json     # ffmpeg/GPU/tool readiness
```

Fresh machine? `mangaeasy setup` provisions everything (GPU-aware; `--all` /
`--minimal` / `--skip <tool>`; re-run to resume). Working dir for a
production run should be the install root — projects live in `library/`,
generated output in `audio/`, `output/`, `work/`.

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
`verify_images`. Open every verify sheet and clear every flag — a red gap
that still shows art or dialogue is a miss. Fix with an overrides file
(`{"07": {"split_at": [23140]}, "12": {"merge": [[4, 5]]}}`) and re-run the
split (previous panels are archived, not lost). Do not proceed to narration
with unresolved suspects.

## 4. Write narration, then verify it

Read each item's panels in order (use `ai-zip` for a labelled bundle, or
`deepseek-ocr2` to add `ocr` text fields) and write
`library/<Project>/<item>/narration.json`:
`[{"image": "<panel file>", "narration": "..."}]` — style rules in
`mangaeasy/assets/prompts/narration.md`. Optional `intro.json` (same shape)
gives chapter 01 a cold-open hook reel.

Verify in two passes:

1. **Structural** — `mangaeasy narration-check --project-root
   library/<Project> --item-range 01-12 --json` must pass: full panel
   coverage, no dangling images, no empty text.
2. **Semantic** — re-read each panel against its narration: is the summary
   faithful, and is every line of dialogue attributed to the correct
   speaker? Fix and re-check before generating audio.

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
normalized to −14 LUFS first). After the run:
`mangaeasy video-validate --project-root library/<Project> ... --json`.
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
   (full control via `--spec` JSON: positions, sizes, arrow, border).
4. **Open the final image at full size** and check text overlap, edges, and
   anything that could read as explicit — fix and re-compose if needed.

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

Needs a prior human `youtube-auth` (see `docs/youtube.md`). Default privacy
is `private` and unaudited API projects are force-locked to it — use
`--privacy public` only when the channel's API project supports it, and
verify the JSON result says the privacy you asked for. Then record the batch
so the plan advances:

```bash
mangaeasy series-mark-published --project-root library/<Project> \
    --items 01-12 --video-id <id from upload> --title "..."
```

## 8. Next batch

Re-run `series-plan` — it now names the next window (13–24, …). Repeat from
step 3 (chapters are already downloaded). When all batches are published,
report the uploaded video URLs and stop.
