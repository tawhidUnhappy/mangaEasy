---
name: manga-video
description: Produce manga recap and image-folder narration videos with MediaConductor, including acquisition, crop verification, narration, TTS, rendering, QA, thumbnails, and explicit YouTube publishing. Use for manga, manhwa, webtoon, comics, panels, chapter recap, or image-to-narrated-video work.
---

# Manga Video

Use `<mc>` from the router: `uv --project <repo> run mediaconductor` for a
source checkout, globally installed `mediaconductor`, or the absolute frozen
executable. If this skill was loaded directly, select that form now. The
examples use `D:/MediaProjects` as the user-owned media workspace. Run from
that workspace or set `MANGAEASY_PROJECT_ROOT` to it for commands such as
`download` that resolve workspace configuration.

Read [references/workflow.md](references/workflow.md), then operate only the
`manga-video` catalog:

```bash
<mc> setup --mode manga-video
<mc> doctor --mode manga-video --json
<mc> commands --mode manga-video --json --full
```

At the narration stage, also read
[references/narration.md](references/narration.md) for the file schema, grounded
authoring rules, and review loop.

Use background jobs for every command marked `long_running`. Prefer the typed
wrapper exposed by the machine catalog:

```bash
<mc> job-start --tool panel_transcript --arguments-json '{"project_root":"D:/MediaProjects/library/example","items":["01"],"device":"auto"}'
```

Treat crop sheets, narration sheets, audio audit, and final validation as
required gates. Never use raw filesystem deletion for generated outputs.
Never publish unless the user explicitly requested it and YouTube status is
connected. Before publishing, read the shared
[`youtube-publishing.md`](../media-conductor/references/youtube-publishing.md),
verify the intended named profile/channel, and pass `--profile <name>` to each
YouTube command.
