---
name: media-conductor
description: Route MediaConductor requests to one isolated production skill and orient an AI agent from a local folder or cloned repository. Use when the user mentions MediaConductor broadly, supplies its repository/folder, asks how to set it up, or has not yet chosen between manga-video, ai-story, and song-video.
---

# MediaConductor router

1. Choose one invocation form and call it `<mc>`:
   - Source checkout: locate the absolute root containing `pyproject.toml` and
     `mangaeasy/`, run `uv sync --project <repo>`, and use
     `uv --project <repo> run mediaconductor`.
   - Global/wheel install: use `mediaconductor` directly.
   - Frozen archive: use the absolute `mediaconductor` executable supplied by
     the user or returned by the installation; no repository or uv sync exists.
2. If the request identifies a mode, run `<mc> modes --mode <mode> --json` and
   read the returned absolute `skill_path`. Use unfiltered `modes --json` only
   when the requested output is genuinely ambiguous.
3. Choose exactly one mode from the user's requested output:
   - Manga panels/chapters/recap: read `../manga-video/SKILL.md`.
   - Written fictional story to narrated scenes: read `../ai-story/SKILL.md`.
   - Song generation or timed lyrics video: read `../song-video/SKILL.md`.
4. Do not load either unselected skill.
5. Install only the chosen mode with `<mc> setup --mode <mode>`.
6. Verify it with `<mc> doctor --mode <mode> --json`.
7. Start MCP with `<mc> mcp --mode <mode> --allow-root <media-workspace>`.
   Add another `--allow-root` only for an intentionally separate project/data
   root. Restart the MCP process to change modes or workspace policy.

Before any mode publishes to YouTube, read
[references/youtube-publishing.md](references/youtube-publishing.md) and select
the intended named account profile explicitly.

Use the legacy `mangaeasy` command only when maintaining an existing script; it is a compatibility alias.
