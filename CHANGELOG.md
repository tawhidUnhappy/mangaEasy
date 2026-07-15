# Changelog

## Unreleased

## 2.0.0 — 2026-07-15

### MediaConductor platform

- Renamed the product, Python distribution, primary CLI, release artifacts,
  and MCP identity to **MediaConductor**. The `mangaeasy` command and Python
  package remain compatibility surfaces for 2.x.
- Added isolated Manga Video, AI Story, and Song Video modes, each with its
  own small MCP catalog, setup profile, Codex-compatible skill, and reference
  documentation.
- Added schema-v2 AI Story projects with immutable character/environment
  cards, ordered scene-state ledgers, deterministic prompt locks, reference
  sheets, digest-bound generation provenance, and explicit visual/video/rights
  gates before publishing.
- Added Song Video projects with ACE-Step 1.5 generation, maintained Demucs
  separation, WhisperX timing against canonical lyrics, minimalistic-sky art,
  and the bundled Edo SZ lyric treatment with a small shadow and line fades.
- Added production/release validation for source, wheel, sdist, frozen CLI,
  and mode-scoped MCP handshakes. Removed tracked sample music and voice media.
- Added an MCP workspace boundary with repeatable `--allow-root` values and a
  startup-directory default, including nested typed jobs and manifest-linked
  media paths.
- Hardened direct CLI child-path inputs: project/MangaDex names, chapter
  folders, panel source/output subpaths and prefixes, and archived-run names
  now reject absolute paths, traversal, reserved names, and non-portable
  characters while preserving valid Unicode and internal spaces.
- Added isolated named YouTube account profiles, allowing each production mode
  to publish to a distinct verified channel or reuse one profile across modes;
  the original single-account files remain the compatible `default` profile.
  One shared Desktop-app client can authorize every profile, and live commands
  automatically open browser re-consent/retry unless `--no-auto-auth` is set.

### Added
- **Background job runner** — `job-start <command> [args…]` runs any command
  as a detached, supervised background job (state + log under `<work>/jobs/`);
  `job-status <id> --json` reports running/succeeded/failed/**orphaned**
  (dead supervisor — machine sleep/kill) with the last `MANGAEASY_PROGRESS`,
  the parsed `MANGAEASY_RESULT`, and a log tail; `jobs --json` lists all.
  Exposed over MCP as `job_start`/`job_status`/`job_list` — long-running MCP
  tools now direct callers there instead of blocking `tools/call` for hours.
- **`commands --json --full`** — the machine-readable catalog now includes
  each command's argument schema (flag, type, required) and a `long_running`
  marker, ending the one-`--help`-per-command discovery loop for agents.
- **`mangaeasy/command_spec.py`** — single declarative table of command
  schemas; the MCP server and `commands --json --full` both render from it,
  so the two surfaces can no longer drift (the MCP server previously kept a
  hand-maintained private copy of every schema).

### Changed
- **`--gpu-workers` is clamped to 4 in code** (was a docs-only rule); the
  tested-unsafe values warn and clamp, `MANGAEASY_UNSAFE_GPU_WORKERS=1` opts
  out on tested hardware.
- **Config loaders raise `ConfigError` instead of `sys.exit`** (the CLI
  dispatcher renders it as `[ERROR] …`, exit 1) and `mangaeasy/config.py` no
  longer mutates `HF_HOME`/`TORCH_HOME` at import time; `tts_pipeline` now
  respects the tool-env cache pin instead of overriding it with a second
  cache at `<cwd>/.hf_cache`.
- **Namespaced root env vars** — `MANGAEASY_ITEMS_ROOT`/`MANGAEASY_AUDIO_ROOT`/
  `MANGAEASY_OUTPUT_ROOT`/`MANGAEASY_WORK_DIR` (bare legacy names still
  honoured).
- **MCP hardening** — JSON reports are parsed by scanning from the last line
  up (a stray print can't blind the parser); truncation keeps head+tail.
- **Docs diet** — CLAUDE.md cut from ~32 KB to ~9 KB (incident lore moved to
  `docs/history/incidents.md`); `START_HERE.md` retired into
  CLAUDE.md/AGENTS.md; stale references (Flask assets, `narration.backup.json`,
  removed packages) purged from live docs.

### Removed
- **Dead GUI-era dependencies and assets** — flask, playwright, cloudscraper,
  curl-cffi, pydub, and beautifulsoup4 were required dependencies with zero
  imports anywhere in the package (leftovers of the deleted GUI/scraper era);
  the unused `[ml]`/`[whisper]`/`[all]` extras (AI deps live in the isolated
  tool envs, never the main env); `mangaeasy/assets/templates/` +
  `mangaeasy/assets/static/` (six orphaned Flask editor pages); the stale
  duplicate `mangaeasy/assets/config/` examples. Classifiers updated
  (Beta, no Flask, Developers).
- **Dead pre-Electron web control center** — `mangaeasy/assets/templates/app.html`,
  its 13-file JS bundle (`static/js/app/*.js`), `static/css/app.css`, and the
  vendored `static/vendor/xterm/` (xterm.js terminal) were leftovers from the
  NiceGUI/pywebview control center that `mangaeasy app` replaced with the
  Electron desktop app (`desktop/`) — the replacement module's own docstring
  already said "The NiceGUI/pywebview GUI this replaced has been removed,"
  but these static assets were never actually deleted. Confirmed unreachable
  (no Flask route in the package renders `app.html`; every other web tool's
  `render_template()` call targets its own distinct template) before removal.

### Added
- **Thumbnail-generation guidance in the recap playbook** — Phase 9 now spells
  out how to write the prompt for high-energy generated (Z-Image Turbo) recap
  key art with a strong focal subject and mobile-readable composition, with a
  non-negotiable safety bound baked into
  the prompt-writing rules themselves: every character drawn as a visibly
  adult, fully clothed (revealing-but-not-explicit is the ceiling), no
  nudity/transparent clothing/explicit content/minor-coded characters, and a
  mandatory-checks item to review every generated variant against those
  rules before picking one — a thumbnail strike risks the whole channel.
- **Z-Image Turbo image generation** — `mangaeasy install-tool z-image-turbo`
  provisions Alibaba's Apache-2.0 text-to-image model (~33 GB) in an
  isolated env, and `mangaeasy zimage --prompt "..." --output out.png`
  generates images (thumbnails, backgrounds, channel art). Hardware is
  handled automatically: full bf16 on 16 GB+ NVIDIA GPUs and Apple
  Silicon, NF4 4-bit quantization on 8–12 GB NVIDIA cards (~24 s/image on
  an RTX 3060), CPU offload/fp32 fallbacks below that. Also exposed as the
  `generate_image` MCP tool. See docs/external-tools.md.
- **`mangaeasy download --chapter N` / `--chapters 0-12 14 20.5`** —
  download any chapter (or a whole batch) without editing config.json.
  Batches fetch the MangaDex feed once, skip chapters that don't exist in
  the requested language (with a warning and a final summary instead of
  aborting), and when several scanlations upload the same chapter number,
  the fullest version (most pages) is picked instead of feed order.
- **Music bed conditioning + ducking in `video-add-bgm` (all default-on).**
  The background-music mix now matches professional voiceover practice
  instead of a flat gain:
  - **Dynamics compression** — the bed's own loudness range is compressed
    (the production track went from LRA 7.9 → 3.4 LU) so it sits at a
    *constant* level under the voice instead of swelling and receding on its
    own, which was the main reason the bed still sounded "unmixed."
    `--no-condition-bed`.
  - **Vocal-band EQ carve** — a gentle dip in the 2–5 kHz
    speech-intelligibility band so the music masks the voice less.
    `--no-eq-carve`.
  - **Sidechain ducking** — the music dips a few dB under the narration and
    breathes back up in the pauses (default ratio 2, tuned so wall-to-wall
    narration doesn't just make the music uniformly quiet). Was opt-in
    `--duck`; now on by default with `--no-duck` to disable.
  - **Limiter fix** — the post-mix `alimiter` no longer runs with its
    default `level=true`, which auto-normalized the output back toward
    0 dBFS and fought the gain staging.
- **Music loudness alignment in `video-add-bgm`** — the (conditioned) music
  stem's integrated loudness is measured (ffmpeg ebur128) and pre-gained to
  the narration's −14 LUFS reference before `--music-volume-db` is applied,
  so the offset is a true LU separation regardless of how hot the track was
  mastered. Disable with `--no-music-loudnorm`. The default offset changed
  −25 → **−22 dB** — the audio-engineering recommendation for dense,
  wall-to-wall narration (recaps); sparser voiceover sits at −18…−20.

### Fixed
- `mangaeasy doctor` reported `gpu_backend: "cpu"` (and the app's Setup tab
  showed "CPU only") on CUDA machines whenever the main env had no torch —
  which is the normal state, since torch lives in the isolated tool envs.
  GPU capability is now probed at machine level (nvidia-smi / Apple
  Silicon), matching what `install-tool` and TTS auto-selection actually
  use; `cuda_device` is filled from nvidia-smi when torch isn't available.

### Added (earlier)
- **`library/<name>/manga.json`** — `mangaeasy download` now records where
  each manga came from: source site, canonical MangaDex title URL, the
  original link you pasted, the canonical title (fetched from the API once,
  then cached), and per-chapter download info (chapter UUID, language, page
  count, timestamp). Previously the link only lived in `config.json`'s
  *current* download target, so it was lost as soon as you moved on to the
  next manga. Existing projects get the file on their next `download` run.
- `mangaeasy library-list` surfaces it: the human view prints `title:` and
  `source:` lines per project; `--json` gains a per-project `manga` field
  (`null` when the file is absent).

## v1.3.1 — 2026-07-03

- Setup tab → YouTube account: the downloaded `client_secret.json` file now
  has its own **Browse client_secret.json…** button (it was a small text
  link before), plus a one-click "Connect with already-attached project"
  button when a project is attached but the account is disconnected.

## v1.3.0 — 2026-07-03

Simpler YouTube project attach + live verification.

### Added
- **Paste-to-attach**: connect your Google project by pasting the Client ID
  and Client secret straight from the Google console — no JSON file needed.
  CLI: `mangaeasy youtube-auth --client-id <id> --client-secret <secret>`;
  GUI: Setup tab → YouTube account now has the two fields + "Attach &
  connect" (the client_secret.json file path still works as before).
- **Live verification**: `mangaeasy youtube-status --verify` (and a
  "Verify" button in the GUI) refreshes the token and queries the channel
  to prove the connection works right now, with a clear error when it
  doesn't. MCP `youtube_status` gained the matching `verify` option.
- Input validation with actionable errors (client-ID format check, both
  values required together).

## v1.2.0 — 2026-07-03

Direct YouTube upload — connect your channel once, then publish finished
videos from the app, the CLI, or an AI assistant.

### Added
- **YouTube account connect** (`mangaeasy youtube-auth` /
  `youtube-status [--json]` / `youtube-logout`, and Setup tab → "YouTube
  account"): browser-based Google consent using your own free OAuth client
  (one-time ~10-minute setup — full walkthrough in `docs/youtube.md`).
  Tokens live in the app's own data folder (`.mangaeasy/youtube/`),
  removable with one click; nothing system-wide.
- **`mangaeasy youtube-upload`**: resumable chunked upload with retry and
  progress, title/description(-file)/tags/privacy/category/thumbnail
  flags, friendly quota/auth error messages, and the standard
  `MANGAEASY_PROGRESS` + `MANGAEASY_RESULT {"video_id","url"}` machine
  contract. Default privacy is **private** (YouTube locks uploads from
  personal, unaudited API projects to private — publish in YouTube Studio).
- **Batch tab → "Upload to YouTube" step**: defaults to your latest joined
  long video, with title (pre-filled), description, tags, and privacy.
- **MCP tools** `youtube_status` and `youtube_upload`; new "Uploading to
  YouTube" section in the AI guide with agent rules (never attempt the
  browser auth; respect quota; don't fight the private lock).
- Dependencies: `google-auth` + `google-auth-oauthlib` (OAuth flow/refresh
  only — the upload itself is plain `requests` against YouTube's resumable
  protocol).

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
