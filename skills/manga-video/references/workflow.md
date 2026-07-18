# Manga Video workflow

The examples use the `<mc>` invocation selected by the parent skill and the
user-owned media workspace `D:/MediaProjects`. Run from the media workspace or
set `MEDIACONDUCTOR_PROJECT_ROOT` to it for workspace-relative commands such as
`download`.

## Project layout

Pass the folder that directly contains chapter/item folders as
`--project-root`. The normal downloaded or imported layout is:

```text
D:/MediaProjects/library/example/     <- --project-root
  manga.json
  01/                                 <- chapter/item
    download/
      001.jpg                         <- source page image
      002.jpg
    panels/                           <- generated crops
    transcript.json                   <- generated panel OCR
    narration.json                    <- reviewed narration
  02/
    download/
      001.webp
```

For existing pages, copy or link them into
`<project>/<chapter>/download/<page image>`. If they must remain in another
folder such as `<chapter>/raw-pages/`, pass
`--source-subdir raw-pages` to `style-detect`, `webtoon-split`, `page-split`,
and `webtoon-cutcheck`. The default is `download`.

## Produce and verify

1. Orient and discover the exact current contract:

   ```bash
   <mc> where --json
   <mc> doctor --mode manga-video --json
   <mc> commands --mode manga-video --json --full
   <mc> library-list --project-root D:/MediaProjects --json
   ```

2. Acquire a title, or prepare the existing-image layout above:

   ```bash
   <mc> download --url "<MangaDex title URL>" --name example --all
   ```

3. Detect the format, inspect the returned sample images, then run exactly one
   crop path:

   ```bash
   <mc> style-detect --project-root D:/MediaProjects/library/example --items 01 --source-subdir download --json
   <mc> webtoon-split --project-root D:/MediaProjects/library/example --items 01 --source-subdir download --work-dir D:/MediaProjects/work
   # For paged manga, use page-split with the same roots and --source-subdir.
   ```

   Both splitters re-check the format per item and refuse a confident
   mismatch (webtoon pages into `page-split` or vice versa), naming the
   correct command; `--force-style` overrides only for deliberate
   mixed-format items.

4. Inspect every returned verification sheet at full readable resolution.
   Apply `webtoon-override` fixes and repeat the split until every crop is
   approved. Never infer approval only from a command's exit code.

   A crop must fully contain its panel — never a partial edge, and never the
   whole page standing in when the panel has its own border. Frame for the
   16:9 landscape video frame the crop will be composited into: a squarish
   (1:1-ish) crop reads fine, but a box far taller than it is wide usually
   swallowed blank gutter above/below the art rather than hugging it, and
   shrinks to an unreadable sliver once fit to 16:9. `page-split` reports
   these as `tall_panel_boxes`; check each against its overlay and, when the
   excess really is gutter, tighten it with `--overrides` (leave it alone
   when the panel is genuinely that tall, e.g. a full-body action shot).

   With the `gemma-4` tool installed, `crop-qa` performs a first automated
   pass over every flagged location — including these framing checks — and
   prints the exact override command per FIX verdict (exit 3 = fixes
   proposed; loop split → crop-qa until 0). It supplements — never replaces —
   reading the sheets yourself.

5. Optionally OCR the panels before writing narration. The narrating agent
   reads bubble text from the panel images themselves; `panel-transcript` adds
   an independent OCR reading that shows up as a cross-check column on the
   review sheets. Run it when text is small/dense, names are uncertain, or the
   narrator cannot see images — skip it freely otherwise (every later gate
   works without `transcript.json`; only a half-finished transcript is flagged
   as an interrupted run). Because it is long-running, use the typed detached
   wrapper and poll the returned id:

   ```bash
   <mc> job-start --tool panel_transcript --arguments-json '{"project_root":"D:/MediaProjects/library/example","items":["01"],"device":"auto"}'
   <mc> job-status <job-id> --json
   ```

6. Maintain the cast registry so speaker attribution stays consistent across
   chapters — `<project-root>/characters.json` via `<mc> characters`
   (`--auto-draft` drafts it with the local Gemma 4 model; review the names
   and set `draft: false`). Use exactly these names in narration.

7. Read [narration.md](narration.md). Write one grounded
   `<chapter>/narration.json`, structurally check it, render semantic review
   sheets, and inspect every sheet:

   ```bash
   <mc> narration-check --project-root D:/MediaProjects/library/example --items 01 --json
   <mc> narration-review-sheets --project-root D:/MediaProjects/library/example --items 01 --work-dir D:/MediaProjects/work --output-root D:/MediaProjects/review/narration
   ```

   Fix incorrect panel descriptions, dialogue, speaker attribution, and spoken
   phrasing; rerun both checks after every edit.

   If you cannot read panel images yourself, `<mc> narrate-auto` drafts the
   narration with the local Gemma 4 model from panels + OCR + the registry
   and then runs both checks; its exit 3 still requires this same review of
   every sheet before TTS.

8. Build using explicit roots. This complete foreground form is useful only
   when the harness can keep a long task alive:

   ```bash
   <mc> video --project-root D:/MediaProjects/library/example --audio-root D:/MediaProjects/audio --output-root D:/MediaProjects/output --work-dir D:/MediaProjects/work --items 01 --tts auto --build-long-video --normalize-audio --no-background-music
   ```

   Prefer the equivalent typed detached job in an ordinary agent session:

   ```bash
   <mc> job-start --tool run_full_pipeline --arguments-json '{"project_root":"D:/MediaProjects/library/example","audio_root":"D:/MediaProjects/audio","output_root":"D:/MediaProjects/output","items":["01"],"tts":"auto","build_long_video":true,"normalize_audio":true,"no_background_music":true}'
   <mc> job-status <job-id> --json
   ```

   `job-start <cli-command> [args...]` remains accepted for existing scripts,
   but `--tool/--arguments-json` is the typed, schema-validated form published
   by `commands --json --full` and MCP. Keep licensed music below narration and
   re-render after any changed panel, narration, or audio input.

   Production defaults to separate `audio_faded/<project>/...` derivatives:
   every panel WAV gets a symmetric 8 ms fade-in and fade-out while the raw TTS
   under `audio/` remains untouched. Use `audio_source: raw` only for an
   intentional diagnostic comparison. With BGM, the order is join → mix music
   → one final two-pass whole-mix normalize to −14 LUFS / −1.5 dBTP. Any music
   change invalidates final normalization.

9. Loop QA until clean, then validate the joined video:

   ```bash
   <mc> work-qa --project-root D:/MediaProjects/library/example --audio-root D:/MediaProjects/audio --output-root D:/MediaProjects/output --items 01 --json
   <mc> video-validate --project-root D:/MediaProjects/library/example --audio-root D:/MediaProjects/audio --output-root D:/MediaProjects/output --items 01 --json
   ```

   `video-validate` is a structural gate (coverage, streams, duration), not a
   complete media review. Also inspect representative start/middle/end frames,
   check narration-to-panel timing, audit faded WAV boundaries for edge clicks,
   and measure the final complete mix at approximately −14 LUFS with true peak
   no higher than −1.5 dBTP.

10. Create and visually inspect a thumbnail. Confirm source, music, voice, and
   upload rights. Only on an explicit publish request, list profiles, verify
   the intended channel with `youtube-status --profile <name> --verify --json`,
   pass the same `--profile <name>` to the upload, and record the returned
   profile, channel id, and video id with `series-mark-published`.

   For a replacement, default to upload new → verify → delete old. Delete-first
   is irreversible and allowed only when the user explicitly requests that
   order. Before deleting, verify the exact profile, live channel id, and old
   video id/title with `youtube-status` and `youtube-list`; preview deletion,
   repeat it with `--confirm --json`, and verify the id is gone. Upload the
   corrected file using the same profile, replace the matching publish record
   (including profile/channel/replaced id when supported), then verify both the
   YouTube listing and `series-plan --json`.

Use absolute project/audio/output/work roots. Preserve `manga.json`,
`publish.json`, source pages, panels, and archived takes.
