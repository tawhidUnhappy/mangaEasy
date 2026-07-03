# Changelog

## v1.1.0 — 2026-07-03

AI-assistant / scripting release: the whole pipeline is now drivable by any
AI agent (or shell script) through a documented, machine-readable CLI
contract — isolation story unchanged.

### Added
- **`docs/ai-guide.md`** — the complete operating manual for AI assistants
  and scripts (install modes, data anatomy, recipes, output contract,
  safety rules), plus a root `AGENTS.md` pointer that agent tools
  auto-discover. A test cross-references the guide against the real command
  catalog so the docs can't rot silently.
- **`mangaeasy mcp`** — a built-in MCP stdio server (pure stdlib, no new
  dependencies) exposing 13 typed tools (doctor, where, library_list,
  video_check, audio_audit, generate_audio, render_videos,
  build_long_video, add_bgm, run_full_pipeline, …) to any MCP-capable
  assistant. Register with `claude mcp add mangaeasy -- mangaeasy mcp`.
- **`mangaeasy commands --json`** — machine-readable catalog of every
  command; **`mangaeasy where --json`** — this install's resolved
  data/tool paths (the first thing an agent should run).
- **`mangaeasy library-list [--json]`** — list projects and per-item
  readiness (panels/narration/intro/audio) without opening the GUI; handles
  both the item-pipeline and legacy chapter layouts.
- **`--json` output** for `video-check`, `video-validate`,
  `video-audio-audit`, and `tools` (joining the existing `doctor` and
  `audio-takes-list`).
- **`MANGAEASY_RESULT {"outputs": [...]}`** — a stable machine-parsable
  final line on successful generation commands (`video`, `video-render`,
  `video-join`, `video-add-bgm`, `video-normalize-audio`) so callers find
  the produced files without scraping log text.
- Setup → About now shows the exact CLI command for this install (with a
  copy button including `MANGAEASY_ROOT`), so agents can share the GUI's
  data and installed tools.
- Agent-style end-to-end test: fixture project → `video-check --json` →
  `video-render` over plain pipes, asserting the result marker.

### Fixed
- **Piped output no longer crashes on Windows**: stdout/stderr are forced
  to UTF-8, so running any command from a script/agent (where stdout is a
  pipe defaulting to cp1252) can't die on characters like "−".

## v1.0.0 — 2026-07-02

First production release. Focus: the downloaded app now actually works as an
installed product on all three platforms, with an honest isolation story.

### Fixed
- **App data location was broken in every packaged build.** The Windows
  portable exe wrote its data (tool environments, models — gigabytes) into a
  temporary folder that changed every launch; on macOS/Linux the app tried to
  write inside the read-only app bundle/AppImage. Data now lives in:
  next to the `.exe` (Windows portable), `~/Library/Application
  Support/mangaEasy` (macOS), `~/.local/share/mangaEasy` (Linux). Electron's
  own caches are kept inside the same folder, so deleting it removes every
  trace.
- **Release assets were always labeled `0.1.0`** regardless of the actual
  version. The build now stamps the git tag's version everywhere and fails if
  the sources disagree.
- **"ffmpeg is bundled" was false** — the release pipeline downloaded ffmpeg
  at build time and then shipped without it. The app now offers a one-time
  ~100 MB "Download core tools" on first launch (Setup tab), with download
  progress, on all three platforms including macOS (no more `brew install`
  requirement).
- Editor launches no longer give up after 15 s (slow antivirus-scanned first
  starts were failing) and no longer leave an orphaned server running on
  timeout.
- Backend JSON replies (doctor, audio takes) are parsed robustly instead of
  breaking on any stray warning line.
- The desktop app's dev-mode backend resolution now works on macOS/Linux
  checkouts, not just Windows.

### Added
- Resizable terminal pane (drag the divider; double-click resets) and
  terminal font-size controls (A− / A+), both remembered across restarts.
- Window size/position remembered across restarts.
- Post-job status line: a failed job shows its exit code prominently instead
  of only in the terminal scrollback.
- Update check: the app notifies (non-intrusively) when a newer release is on
  GitHub. Setup tab → About also checks on demand.
- About section in Setup: version, where the app's data lives (with an Open
  button), and an "Open logs folder" button.
- Main-process log file (`.mangaeasy/logs/main.log`) and a renderer error
  boundary — UI crashes show an error page with details instead of a blank
  window.
- Test suite (pytest) for the pipeline's pure logic, ruff linting, and a CI
  workflow that runs lint/tests/typecheck/build on every push on all three
  OSes. The release build smoke-tests the frozen backend before packaging.
- Intel-mac build (best-effort) alongside Apple Silicon.

### Changed
- Release artifacts renamed to one convention:
  `mangaEasy-<version>-<os>-<arch>[...]`.
- The `.deb` package metadata (maintainer, category) is now real.
