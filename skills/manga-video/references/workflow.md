# Manga Video workflow

The examples use the `<mc>` invocation selected by the parent skill and the
user-owned media workspace `D:/MediaProjects`. Run from the media workspace or
set `MANGAEASY_PROJECT_ROOT` to it for workspace-relative commands such as
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

4. Inspect every returned verification sheet at full readable resolution.
   Apply `webtoon-override` fixes and repeat the split until every crop is
   approved. Never infer approval only from a command's exit code.

5. OCR every panel before writing narration. Because this is long-running,
   use the typed detached wrapper and poll the returned id:

   ```bash
   <mc> job-start --tool panel_transcript --arguments-json '{"project_root":"D:/MediaProjects/library/example","items":["01"],"device":"auto"}'
   <mc> job-status <job-id> --json
   ```

6. Read [narration.md](narration.md). Write one grounded
   `<chapter>/narration.json`, structurally check it, render semantic review
   sheets, and inspect every sheet:

   ```bash
   <mc> narration-check --project-root D:/MediaProjects/library/example --items 01 --json
   <mc> narration-review-sheets --project-root D:/MediaProjects/library/example --items 01 --work-dir D:/MediaProjects/work --output-root D:/MediaProjects/review/narration
   ```

   Fix incorrect panel descriptions, dialogue, speaker attribution, and spoken
   phrasing; rerun both checks after every edit.

7. Build using explicit roots. This complete foreground form is useful only
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

8. Loop QA until clean, then validate the joined video:

   ```bash
   <mc> work-qa --project-root D:/MediaProjects/library/example --audio-root D:/MediaProjects/audio --output-root D:/MediaProjects/output --items 01 --json
   <mc> video-validate --project-root D:/MediaProjects/library/example --audio-root D:/MediaProjects/audio --output-root D:/MediaProjects/output --items 01 --json
   ```

9. Create and visually inspect a thumbnail. Confirm source, music, voice, and
   upload rights. Only on an explicit publish request, list profiles, verify
   the intended channel with `youtube-status --profile <name> --verify --json`,
   pass the same `--profile <name>` to the upload, and record the returned
   profile, channel id, and video id with `series-mark-published`.

Use absolute project/audio/output/work roots. Preserve `manga.json`,
`publish.json`, source pages, panels, and archived takes.
