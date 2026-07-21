---
name: manga-recap
description: >
  Produce narrated manga/webtoon recap videos for YouTube with the mediaconductor
  CLI: download a series from a MangaDex URL, crop panels (webtoon or paged),
  verify crops, write and verify narration, generate TTS audio, render and
  join videos, mix background music, generate a thumbnail, and upload — in
  12-chapter batches. Use when the user gives a MangaDex URL, asks for a
  manga/manhwa/webtoon recap video, or asks to continue/publish the next
  batch of an existing recap series.
---

# Manga recap production (mangaEasy)

You drive the whole pipeline through the `mediaconductor` CLI (or its MCP tools —
same engine). Full reference: `docs/manga-video-guide.md`; discover any command's
flags with `mediaconductor commands --json --full` (schemas + `long_running`
markers — no per-command `--help` needed). Machine contract: every `--json`
command prints one JSON object; generation commands end with a
`MEDIACONDUCTOR_RESULT {...}` line; exit 0 = ok, 1 = failure, 2 = usage error;
nothing ever prompts for input.

**Hard safety rules** — never delete/rename anything inside `library/`
source items; edit narration via `narration-edit`, not by hand; clear
generated output only with `video-clean-*` (everything else auto-archives to
`old/run_NNNN/`). `--gpu-workers` is clamped to 4 in code — don't fight it.

**Run long steps in the background, then wait — don't burn compute polling.**
`download`, `page-split`/`webtoon-split`, `panel-transcript`, `video`,
`zimage`, and `youtube-upload` each run for minutes to tens of minutes. Launch
each as a background job and stop; let the harness's completion notification
wake you instead of sleeping or re-checking in a loop. No harness backgrounding
(e.g. MCP-only)? Use the built-in runner: `mediaconductor job-start <command>
[args…]` returns a job id instantly; poll `mediaconductor job-status <id> --json`
(status/progress/result; reports `orphaned` if the machine slept). GPU tools
(MAGI, DeepSeek-OCR, IndexTTS, Z-Image) block-buffer stdout, so their logs look
empty until the end — judge health from filesystem signals (growing
panel/transcript counts, output files appearing, `nvidia-smi`), not by tailing
the log. Only foreground the quick `--json`/validation commands.

## 0. Orient (every session)

```bash
mediaconductor where --json      # install paths + version
mediaconductor doctor --json     # ffmpeg/GPU/tool readiness
mediaconductor work-status --project-root library/<Project> --json   # resuming? exact per-item stage
```

Resuming a project — including picking it back up on a **different LLM**
after another one ran out of budget or context mid-batch — or working
alongside other agents? Follow `docs/multi-agent.md`: `work-status --next`
names the unclaimed actionable tasks, `work-claim` leases an item+stage (and
`--resource gpu` serializes the GPU model tools), `work-note` shares
character names/speaker conventions between narrators, `work-todo` is the
shared plan-level checklist (batch scope, redo requests, things to confirm)
that outlives any one context window, and `mediaconductor work-qa` is the
fix-until-clean gate — loop `work-qa → apply the listed fix → work-qa` until
exit 0. `work-artifacts` lists what already exists for reuse before you
regenerate anything. All of it is plain files under
`library/<Project>/.workboard/`, not chat state, so any agent on any model
reads the exact same picture. Set `MEDIACONDUCTOR_AGENT` (e.g. `claude-fable`,
`gpt-5.6`) so claims/notes/todos show which model did what.

Fresh clone/machine? Follow the agent runbook in `docs/setup.md`:
`uv sync` → `mediaconductor setup` (GPU-aware; `--all` / `--minimal` /
`--skip <tool>`; re-run to resume) → verify `doctor --json` → `mediaconductor
smoke-test` (renders and checks a tiny real video; `SMOKE TEST PASS` = the
machine can produce videos). Working dir for a production run should be the
install root — projects live in `library/`, generated output in `audio/`,
`output/`, `work/`.

## 1. Download the series (user gives a MangaDex URL)

```bash
mediaconductor download --url "<mangadex title url>" --all
```

Polite by design (rate spacing, backoff, jitter) — never parallelize
downloads or shrink its delays. `--name <Project>` overrides the derived
folder name; `--from/--to` bound the range. Re-running resumes; complete
chapters are skipped. The result line gives the project path
(`library/<Project>/`), and `manga.json` records the source.

## 2. Plan the batch

Videos ship 12 chapters at a time (01–12, then 13–24, …):

```bash
mediaconductor series-plan --project-root library/<Project> --json
```

Work on `next_batch` only. If it's partial, the series may have ended
(fine — ship what exists) or later chapters aren't downloaded yet.

## 3. Decide the crop tool, then crop and VERIFY

```bash
mediaconductor style-detect --project-root library/<Project> --json
```

Open 2–3 of the returned `sample_images` and confirm the verdict yourself:
endless vertical strips → `webtoon-split`; discrete pages with panel grids →
`page-split` (needs `install-tool magi-v3`). For paged sources the panel
reading order is auto-resolved from the language `download` recorded in
`manga.json` (ja / zh-hk → right-to-left; ko / zh / en → left-to-right) and
announced as `[page-split] reading direction: …` — sanity-check that line
and override with `--reading-direction rtl|ltr` if the source metadata is
wrong. Then crop the batch, e.g.:

```bash
mediaconductor webtoon-split --project-root library/<Project> --item-range 01-12
```

**The crop double-verify loop** (details: `docs/operate/crop-verify-narrate.md`):
the result lists per-item `suspects` / `content_drops` and the exact
`verify_images`. For webtoons, then run the full-resolution pass — judging
crops on downscaled sheets alone has shipped sliced bubbles before:

```bash
mediaconductor webtoon-cutcheck --project-root library/<Project> --item-range 01-12
```

Read EVERY sheet it writes; FIX any cut through a figure/speech bubble and
any bubble/SFX-fragment short panel by adding the fix with `webtoon-override`
(never compute merge indices by hand — it resolves them from the manifest):

```bash
mediaconductor webtoon-override --file work/overrides.json \
    --project-root library/<Project> --item 07 --merge-at-cut 23140
# fuse sheet panels #4..#5:            --item 12 --merge-panels 4,5
# reposition a bad cut:                --merge-at-cut 42186 --split-at 42394
```

ACCEPT background/effect-art cuts, bordered thin scenery, scanlator
banners. Re-run the split with `--overrides work/overrides.json`, then
re-run cutcheck to confirm. Do not proceed to narration with unresolved
suspects.

**Re-cropping after narration exists?** Never re-narrate: `mediaconductor
panels-remap --project-root library/<Project> --item-range 01-12` (dry run,
then `--apply`) carries narration texts and WAVs to the new numbering, then
review its `shift`/`merge` list with `narration-review-sheets
--only-images ...` and rebuild with `mediaconductor video --overwrite-video`.

## 4. Write narration grounded in the panels, then verify it

Narration is written by YOU, from the panel images — Read every panel of the
chapter and take the bubble text from what you see. OCR is **optional**: if
you want a second, independent reading (small/dense text, doubtful names, or
a non-vision agent doing the narrating), run panel-transcript first (needs
`install-tool deepseek-ocr2`) and its text appears as a cross-check column on
the review sheets:

```bash
mediaconductor panel-transcript --project-root library/<Project> --item-range 01-12
```

Skipping it skips nothing else — every gate below works with or without
`transcript.json` (a *half-finished* transcript is flagged by work-qa as an
interrupted run: finish it or delete it). Write
`library/<Project>/<item>/narration.json`
(`[{"image": "<panel file>", "narration": "..."}]`) from the **panel image**
(+ transcript when present) — style rules in
`mediaconductor/assets/prompts/narration.md`. Optional `intro.json` (same shape)
gives chapter 01 a cold-open hook reel — it is **prepended** before that
chapter's `narration.json`, so its panels must be ones the chapter's
`narration.json` does **not** also use, or they play twice (the cold-open
replays a beat, then it shows again in-context — a viewer-reported "why is the
start repeating?"). Either give the intro its own distinct panels, or drop
those panels from `narration.json`; `narration-check` now fails on the overlap.
Grounding rules (each traces to real viewer complaints about a shipped recap):

- **one beat per panel** — the line describes THAT panel, never a summary of
  several panels smeared over one image;
- **paraphrase anchored to the bubble text** — reword freely, but the meaning
  must match what the panel actually says (use the OCR transcript as
  cross-evidence when it exists);
- **speakers attributed from the panel** (who is on-panel, whose bubble
  tail) — if unsure, narrate without naming;
- **no punctuation-only lines** (`"?!"` → near-empty TTS audio; video-check
  flags these as unspeakable); never end on a bare em dash/hyphen with no
  closing word (`"...Ah—"`) — finish the sentence, or use an ellipsis for a
  genuine trail-off. Note: an occasional TTS tail-click is a
  generation-random model artifact seen across ordinary, well-formed lines
  too — `video-fade-audio`'s adaptive declick (see CLAUDE.md) is the actual
  fix, not narration wording;
- **narrate the emotion, never the stammer** — manga letters a stutter or a
  cut-off word to show feeling (`"Th- This is...?"`, `"I... I guess..."`,
  `"W... w... well..."`), but spoken aloud that is a defect, not emotion: the
  voice re-articulates each fragment and sounds broken. Write what the panel
  means (`"he stares, startled"`, `"she answers reluctantly"`). Same for
  content-free fragments (`"Huh..."`, `"Um..."`), two ellipses in a row, and
  repeated words. `work-qa` rejects these as `narration:fluency` and the
  TTS/render preflight refuses to build until they are fixed;
- **no name before the story gives it** — if the hero names a dragon on page
  14, earlier lines say "the dragon"; naming it sooner spoils the scene;
- **optional `"emotion"` field** on the few lines that earn it: the value must
  be **exactly** `"calm"`, `"neutral"`, `"slightly sad"`, or `"slightly happy"`
  — IndexTTS2 renders anything stronger as an actual shouted performance, so
  high-intensity hints are rejected by QA. Most lines carry no field at all;
  full rules in `mediaconductor/assets/prompts/narration.md`.

Verify in two passes:

1. **Structural** — `mediaconductor narration-check --project-root
   library/<Project> --item-range 01-12 --json` must pass (`ok:true`): no
   dangling images, no empty text, no intro/narration overlap. Panels with no
   narration entry are reported as **warnings**, not failures — deliberately
   skipping credits/title banners, scanlator pages, SFX-only frames, and
   duplicate reaction beats is correct (the renderer builds the video **only**
   from narrated panels). Confirm the uncovered list is exactly those skips,
   not a story beat you forgot.
2. **Semantic** — `mediaconductor narration-review-sheets --project-root
   library/<Project> --item-range 01-12`, then Read EVERY sheet (panel +
   narration + OCR side by side) and check the grounding rules above.
   Fix each bad line with one command (stale WAV pruned automatically):
   `mediaconductor narration-edit --project-root library/<Project> --item 01
   --set <image> "<new line>" --prune-audio`. Use `--delete <image>`,
   `--list`, `--intro`, or `--set-json '[...]'` for bulk edits — no
   hand-editing of narration.json needed.

## 5. Audio → render → join → music

```bash
mediaconductor video --project-root library/<Project> --audio-root audio \
    --output-root output --item-range 01-12 --tts auto \
    --build-long-video --normalize-audio \
    --background-music "<music file>"
```

`--tts auto` uses IndexTTS (voice cloning) when an NVIDIA GPU + model +
speaker WAV are available, otherwise Kokoro. Music is mixed low under the
narration by design — conditioned, loudness-aligned, side-chain ducked at
`--music-volume-db` −30 dB default, tuned to stay comfortable over a long
watch (keep within −20…−32; narration is
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
`mediaconductor video-validate --project-root library/<Project> ... --json` —
`warnings` (unnarrated panels, orphan audio) are informational; anything in
`errors` blocks upload.
Full recipe + troubleshooting: `docs/recap-video-playbook.md`.

## 6. Thumbnail (1280×720)

1. Generate key art (style rules and the platform-safe prompt shape are in
   `docs/thumbnail.md` — visibly adult, fully clothed, suggestive-ceiling,
   no text in the image):
   `mediaconductor zimage --prompt-file thumb_prompt.txt --output thumb.png
   --width 1280 --height 720 --count 4`
2. Open all variants, pick the best (faces and hands intact).
3. Add text furniture — 1–3 blocks, 3–5 punchy words each, highlighting one
   shocking fact from the batch:
   `mediaconductor thumbnail-compose --base thumb_03.png --output final_thumb.png
   --text "HE ATE A GOD?!" --text "CH 1-12"`
   (full control via `--spec` JSON: blocks/arrows/border). Make the markup
   read hand-placed: tilt the big hook block (`"rotate": -3…-5`), use the
   default fat outlined block-arrows (width ≈ 22–30) instead of thin lines,
   keep the drop shadow on.
4. **Open the final image at full size** and check text overlap, edges, and
   anything that could read as explicit — fix and re-compose if needed.
5. Iterating after upload? Reuse the exact verified account with
   `mediaconductor youtube-thumbnail --profile <profile> --video-id <id>
   --image final_thumb.png` so a multi-channel install cannot target the
   legacy `default` account accidentally.

## 7. Title, description, upload

Title ≤ 100 chars: hook + series name + chapter range, front-load the hook
(e.g. "He Ate a God and Leveled Up — <Series> Recap Chapters 1–12").
Description: 2–3 sentence spoiler-light hook, then chapter range, then
5–10 search phrases people actually type. Tags: comma-separated
series/genre terms.

```bash
mediaconductor youtube-upload --profile <profile> \
    --video output/<Project>/<Project>_full.mp4 \
    --title "..." --description "..." --tags "manga,recap,..." \
    --thumbnail final_thumb.png --privacy public --json
```

Before constructing the upload, run `mediaconductor youtube-profiles --json`.
It is offline, exposes no token/client contents, and reports the one shared
Desktop-app client path. Match the requested destination to the cached channel
title/id and ask the user if more than one profile is plausible; never infer a
channel from the profile name. Pass the selected profile explicitly even when
it is `default`.

Check that exact account with `mediaconductor youtube-status --profile
<profile> --verify --json`. With the shared client present, a missing, expired,
revoked, or API-rejected token opens Google browser consent automatically; the
agent starts the call, waits for the channel owner to approve it, and lets the
same call continue. Use `--no-auto-auth` only on a headless worker. Never read
client/token JSON into context. Default privacy is `private` and
unaudited API projects are force-locked to it — use `--privacy public` only
when the channel's API project supports it, and verify the JSON result says the
privacy you asked for. Then record the batch so the plan advances:

```bash
mediaconductor series-mark-published --project-root library/<Project> \
    --items 01-12 --video-id <id from upload> --title "..."
```

## 8. Next batch

Re-run `series-plan` — it now names the next window (13–24, …). Repeat from
step 3 (chapters are already downloaded). When all batches are published,
report the uploaded video URLs and stop.

**Stopping mid-batch (context/budget ran out, or handing off to a different
LLM)?** Before you go: `work-note --topic handoff --add "<exactly what you
were mid-step on, e.g. item 07 render was running, verify job-status before
re-launching>"`, and `work-todo --add "<next concrete step>"` for anything
not yet visible on disk (a redo request, a decision still pending). The next
agent's step 0 (`work-status --json`, which surfaces both) picks this up
automatically — same continuity whether that agent is you again or a
completely different model.
