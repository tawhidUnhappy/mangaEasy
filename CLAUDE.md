# mangaEasy — guide for AI agents working on this codebase

This file is the onboarding doc for any AI (Claude or otherwise) picking up
work on `D:\mangaEasy`. It describes what the app does, how the pieces fit
together, the conventions that matter, and the gotchas that have bitten past
sessions. Read this before making changes. `START_HERE.md` is the repo entry
map; `docs/architecture.md`, `docs/install.md`, `docs/external-tools.md`, and
`docs/publishing.md` are supplementary. If the task is *cropping/narrating*,
follow `docs/operate/crop-verify-narrate.md`; if it is *producing a recap
video* end to end, follow `docs/recap-video-playbook.md` — the verified
production recipe (download → panels → narration → build → thumbnail → upload).

## What this app does

mangaEasy turns a manga chapter (a folder of panel images + a narration
script) into a narrated video, and can chain many chapters into one long
"recap" video with background music, ready for YouTube.

**mangaEasy is a CLI + MCP tool for LLM agents — there is no GUI.** The whole
surface is the `mangaeasy` command (~50 subcommands, each with a machine-readable
`--json` / marker contract) plus `mangaeasy mcp`, which exposes the same engine
as typed MCP tools. A previous Electron desktop app and a set of Flask web
editors were removed — see `docs/history/legacy-inventory.md`. New here?
`START_HERE.md` is the entry map.

## The one-CLI pattern

Everything is dispatched from `mangaeasy/cli.py`'s `COMMANDS` dict:
`command name -> (module path, function name, help-group, one-line help)`.
Modules are imported **lazily** (only when that command runs), so
`mangaeasy --help` stays instant and never pulls in torch/opencv.

To add a new command: write a module with a `main()` (or similarly named)
function that does its own `argparse`, then add one line to `COMMANDS`.
`mangaeasy.runtime.cli_command(...)` builds an argv list for one CLI command
so pipeline code can shell out to another subcommand instead of importing it
directly (see `run_pipeline.py`).

The video build is the **item-based pipeline** (`video-*` / `video_pipeline/`):
"item" = one source unit (usually one manga chapter) that becomes one short
video, later joined into a long video. (The older chapter-based render/audio
commands and the web editors were removed in the CLI-only reorg; if you find a
reference to `render-video`, `add-bgm`, `join-chapters`, a `narration-editor`,
etc., it is stale — the live path is `video-*`.)

## Data layout (a "project")

A project is a folder containing chapters/items, conventionally under
`library/<project-name>/<item>/`:

```
library/<project-name>/
  manga.json                 source record, written by `mangaeasy download`:
                             site, title URL, manga_id, canonical title,
                             per-chapter download info (see below)
  01/
    panels/                  source panel images (png/jpg/webp)
    narration.json            [{"image": "chapter1_001.png", "narration": "..."}]
    intro.json                OPTIONAL: same shape, prepended at load time
    download/                 raw downloaded chapter assets (MangaDex etc.)
  02/
    ...
```

- `mangaeasy.video_pipeline.item_assets.load_narration(item_dir)` is the
  **single source of truth** for reading an item's narration: it reads
  `narration.json` and, if `intro.json` exists, prepends those entries.
  `intro.json` is how one item (usually the first chapter) gets a cold-open
  trailer/hook reel without splicing it into the real script. **Every
  caller must go through this function** — there have been bugs in the past
  from modules that had their own narration-loading copy that didn't know
  about `intro.json`. If you add a new place that needs narration entries,
  import `load_narration`, don't re-implement it.
- Item folder names are sortable strings (`01`, `02`, ...); item selection
  syntax across the CLI is `--items 01 02 05-08` or `--item-range 01-12`
  (parsed by `expand_item_tokens` / `merge_item_selection` in
  `video_pipeline/common.py`).
- `manga.json` (project root, machine-managed) answers "where did this manga
  come from?" — `mangaeasy download` writes/merges it on every run
  (`update_manga_json()` in `mangaeasy/download/mangadex.py`): `source`,
  canonical `url` (`https://mangadex.org/title/<uuid>`), the original
  `source_url` the user pasted, `title` (fetched from the API once, then
  cached), and a `chapters` map (`chapter_id`, `language`, `pages`,
  `downloaded_at`). `library-list` surfaces it (human view prints
  `title:`/`source:` lines; `--json` has a per-project `manga` field, null
  when absent). Config.json only holds the *current* download target, so
  without this file the link of previously downloaded manga was lost.

Generated output lives in separate root folders (override via env vars or
`--*-root` flags), never inside `library/`:

- `audio/<project>/<item>/<panel-stem>.wav` — one narration WAV per panel.
- `audio/<project>/_items/item_<NN>_narration.wav` — per-item concatenated
  narration track (used when joining into the long video).
- `output/<project>/<item>/` — rendered per-item videos.
- `output/<project>/<project>_full.mp4` — the joined long video.
- `work/` — scratch directory, safe to delete (`video-clean-work`).

Default roots: `DEFAULT_PROJECT_ROOT=content`, `DEFAULT_AUDIO_ROOT=audio`,
`DEFAULT_OUTPUT_ROOT=output`, `DEFAULT_WORK_DIR=work` (see
`video_pipeline/common.py`), overridable by env vars (`PROJECT_ROOT`, etc.)
or `--project-root`/`--audio-root`/`--output-root`/`--work-dir` flags. Agents
should pass explicit `library/<project>` etc.

## Archive-before-overwrite (never silently destroy output)

`mangaeasy/utils/__init__.py` has `archive_before_overwrite()` /
`archive_into_run()` / `LazyArchiveRunDir`. Any pipeline step that's about to
overwrite previously generated audio or video moves the old file into
`old/run_NNNN/...` first, instead of deleting it. This pattern is used
throughout `video_pipeline/` (audio regeneration, long-video rebuilds, BGM
remixing). `audio-takes-list` / `audio-takes-restore` (backed by
`video_pipeline/audio_takes.py`) let a user browse and restore an old take
instead of regenerating. **When writing new code that overwrites generated
output, use this pattern — don't `Path.write_bytes()` over an existing
file.**

`video-clean-all` / `video-clean-audio` / `video-clean-video` /
`video-clean-work` delete *generated* output only; they never touch
`library/` source chapters.

## The item video pipeline, end to end

`mangaeasy video` (→ `video_pipeline/run_pipeline.py`) is the all-in-one
entry point. It shells out to the narrower commands in this order:

1. **Audio** — `video-audio` (Kokoro, in-process worker pool) or
   `video-audio-indextts` (IndexTTS, external tool env). `--tts auto`
   picks IndexTTS only if: an NVIDIA GPU is present, the `index-tts` tool env
   is installed, model checkpoints exist, and a speaker reference WAV
   resolves — otherwise it falls back to Kokoro. See
   `resolve_tts_engine()` in `run_pipeline.py`.
2. **(optional) Fade** — `video-fade-audio`, only if `--audio-source faded`;
   writes fade-in/out copies to a sibling `_faded` audio root, never touches
   the raw audio.
3. **Render** — `video-render` (`video_pipeline/make_videos.py` /
   `item_video_builder.py`): one video per item from panels + per-panel
   audio, frame-aligned to audio duration (`frame_aligned_duration()` in
   `item_assets.py` rounds each panel's visible time up to a whole frame
   count so audio never gets cut off).
4. **(optional) Join into a long video** — only if `--build-long-video`.
   **This always runs in three separate steps, never one combined ffmpeg
   call**, specifically so that re-mixing background music doesn't require
   re-joining every item clip from scratch:
   - `video-join` (`long_video_builder.py` / `make_long_video.py`) — joins
     item videos into one long video, **with no background music**, always.
   - `video-normalize-audio` (only if `--normalize-audio`) — two-pass
     loudness normalization to −14 LUFS (YouTube target), replaces in place.
   - `video-add-bgm` (only if `--background-music` is set) —
     `video_pipeline/add_long_video_bgm.py` mixes a music track into the
     *already-joined, already-normalized* long video via ffmpeg
     `amix`/`alimiter`, archiving the previous file first. This is the step
     to re-run alone when a user just wants to try a different track or
     volume — it's far cheaper than re-joining. Before mixing, the track is
     QC'd and repaired automatically (`video_pipeline/music_bed.py`): a
     20 ms RMS envelope scan finds sub-window splice holes that
     `silencedetect` can't see, silent lead/tail is trimmed, and when the
     track is defective or shorter than the video it's replaced by a
     crossfade-looped seamless bed cached under `<work-dir>/music_bed/`.
     This exists because a raw `-stream_loop -1` of a rip with ~80 ms
     splice holes shipped a public video with audible music cut-outs
     (2026-07-06) that had to be replaced. `--raw-music` bypasses it;
     bed preparation failures fall back to the raw file rather than
     breaking the mix.

   The ordering (join → normalize → add BGM) is deliberate: narration
   loudness gets normalized to target on its own, *then* music is layered on
   top at a fixed dB offset below it — normalizing after mixing would pull
   the music up to the same loudness as narration.

Background music volume is **dB-native** end to end (`--music-volume-db`,
default `-22.0`, applied via ffmpeg's `volume=XdB` filter) — not a linear
multiplier. Don't reintroduce a linear volume knob; it was deliberately
converted away from one because it confused users (the UI used to label a
linear value "dB").

**The music bed is conditioned in three stages before it is placed under
the voice** (all on by default; each independently disable-able). The order
matters — dynamics first, then measure, then offset — so the offset stays a
true, consistent separation:

1. **Dynamics + spectrum (`condition_bed()` in `music_bed.py`).** A raw
   track carries its own 6–10 LU loudness range (the Thapin production bed
   measured **LRA 7.9 LU**, a 37 LU momentary swing). A flat gain preserves
   all of it, so the bed audibly swells and recedes *independently of the
   narration* — the single biggest reason a bed sounds "unmixed," and the
   defect that prompted this work. `condition_bed()` bakes an `acompressor`
   (LRA 7.9 → ~3.4, verified) plus a gentle `equalizer` dip in the 2–5 kHz
   vocal band into a cached copy. `--no-condition-bed` / `--no-eq-carve`.
2. **Loudness alignment (`music_loudnorm_pregain()`).** The *conditioned*
   bed's integrated loudness (ffmpeg `ebur128`) is pre-gained to the same
   −14 LUFS reference the narration is normalized to (clamped ±12 dB,
   `--no-music-loudnorm`). Measuring the conditioned bed — not the raw file
   — is why `--music-volume-db` stays a true LU separation regardless of the
   source's mastering.
3. **Sidechain duck (`build_mix_filter()`).** The narration side-chains a
   gentle `sidechaincompress` on the music so it dips a few dB under speech
   and breathes back up in the pauses (the radio/podcast/DaVinci workflow).
   For **wall-to-wall recap narration the ratio must stay low** (default 2):
   a high ratio makes ducking degenerate into a uniform reduction that just
   makes the music quiet everywhere (measured 9 dB at ratio 4 on a
   continuous-narration segment) instead of dipping. `--no-duck`.

The **−22 default offset** is the audio-engineering recommendation for
*dense, wall-to-wall* narration (recaps): the general voiceover range is
−18…−20, but continuous speech masks more, so the guidance is to push toward
−22 (Pure Audio Insight's "e-learning/dense-information" figure). −15 masks
the voice on phone speakers, −25 is the inaudibility floor — keep new
volume-related defaults inside −18…−24. (Was −19; a real listen on a recap
found it a touch loud, matching the dense-narration guidance.)

Two ffmpeg-filter invariants in `build_mix_filter()` are load-bearing and
each silently undid the −14 LUFS target once — both are guarded by tests in
`test_music_bed.py`, **keep them**:

- **`amix=…:normalize=0`.** amix's default rescales every input by 1/inputs
  (−6 dB for two), which shipped ~−20 LUFS videos (YouTube never boosts
  quiet uploads, so they just played quiet). Plain summation keeps the
  narration at its normalized loudness; the `alimiter` handles summed peaks.
- **`alimiter=level=disabled`.** alimiter's default `level=true`
  auto-normalizes the output back toward 0 dBFS, fighting the whole gain
  chain and pushing the mix hotter than intended. Disabled, the limiter is a
  pure peak-safety catch.

## GPU / TTS concurrency — known limits

- Kokoro runs as `gpu-workers` parallel processes, each loading its own
  model copy onto the GPU (`video_pipeline/generate_audio.py` shards the
  item manifest across workers with `chunk_list()` /
  `kokoro_batch_worker.py` does the actual generation in each worker).
- `torch.backends.cudnn.benchmark` **must stay `False`** in
  `kokoro_batch_worker.py` — `True` causes `CUDNN_STATUS_EXECUTION_FAILED`
  under concurrent multi-process GPU access (cuDNN re-benchmarking races
  across processes).
- Empirically, on an RTX 3060, **`--gpu-workers 4` is stable; `8` crashes**
  even with `benchmark=False` (confirmed in real production runs, not just
  synthetic tests) — 8 concurrent CUDA contexts exceeds reliable capacity on
  that card. Treat 4 as the practical ceiling unless tested otherwise on
  different hardware.
- GPU/CPU/RAM usage climbing over a long run is **expected, not a leak** —
  PyTorch's CUDA caching allocator never returns memory to the OS on its
  own, and narration text length varies, so allocations don't perfectly
  recycle. Mitigated with periodic `gc.collect()` +
  `torch.cuda.empty_cache()` every `CACHE_RELEASE_INTERVAL = 25` items in
  `kokoro_batch_worker.py`. If usage growth becomes a complaint again, raise
  that interval's frequency rather than assuming a new leak.
- Resuming an interrupted run: `--resume-audio` deletes the most-recently
  written audio file plus the previous 5 (in case the last write was
  mid-flight) before regenerating. With `--gpu-workers > 1`, the manifest is
  sharded *before* resume-pruning runs, so pruning must happen **per shard**,
  not against one global "last N" — that's what
  `prune_recent_audio_for_resume(..., shards=args.gpu_workers)` /
  `chunk_list()` in `video_pipeline/common.py` exist for. If you change
  sharding logic, keep resume-pruning shard-aware or multi-worker resume will
  prune the wrong files.
- HF Hub model loading tries `HF_HUB_OFFLINE=1` first and only falls back to
  online on failure (`build_pipeline()` in `kokoro_batch_worker.py`) — avoids
  a redundant network freshness check on every single run once the model is
  cached locally.

## Pre-flight validation tools

- `video-check` — validates item inputs exist (panels + narration.json)
  before generation.
- `video-validate` — checks generated audio/video against inputs after the
  fact.
- `video-audio-audit` (`video_pipeline/audio_audit.py`) — ffprobes every
  expected per-panel audio file, separately reporting **missing panels**
  (a data problem, needs human attention) vs **missing/corrupt audio**
  (`< MIN_AUDIO_SECONDS = 0.05s` counts as corrupt) — regeneratable. Pass
  `--fix` to delete bad audio files (never touches panels or narration.json)
  so the next `video-audio` run regenerates exactly those. Skips items that
  aren't ready yet (no `narration.json`) by logging instead of crashing.
  Run this before any long-video build if you don't trust the audio state.

## Config files

- `config.json` (project root) — per-project settings: manga download
  source, current chapter, BGM file path, TTS speaker WAV path. Small,
  user-facing.
- `config.system.json` (project root, or `.mangaeasy/` in an installed app)
  — machine-wide defaults: audio sample rate/fades, BGM file + volume_db,
  video encoder settings (NVENC/libx264 presets, bitrate), watermark, whisper
  settings. `config.system.example.json` is the template for a fresh install.
- Both load through `mangaeasy/config.py`.

## External AI tool environments (`mangaeasy/tools/`)

Kokoro, IndexTTS, MAGI (panel detection), DeepSeek-OCR 2, and Z-Image Turbo
(image generation, `mangaeasy zimage`) each live in their own
isolated `uv` project under `<install>/.mangaeasy/tools/<tool>/` so their
CUDA/Torch/Transformers versions can't conflict with the main package or
each other. Z-Image facts that must not be "optimized" away: guidance_scale
stays 0.0 (Turbo has no CFG), bf16/fp32 only (fp16 renders black frames),
NF4 quantization is what lets it run on 8–12 GB GPUs. `mangaeasy install-tool <name>` installs one;
`mangaeasy.tools.external.resolve_tool_dir()` finds an installed tool's
directory; `mangaeasy.tools.vendored` vendors ffmpeg/uv/git-lfs into
the install so end users never need them on PATH —
`ensure_vendored_path()` runs unconditionally at the top of `cli.py` so every
bare subprocess call (`"ffmpeg"`, `"ffprobe"`, ...) picks up the vendored copy
automatically. See `docs/external-tools.md` and `docs/install-tools.md` for
the install mechanics; this file just covers what calls what.

`tool_env()` (in `tools/external.py`) is the env for every tool subprocess.
It **force-pins** `HF_HOME`/`HF_HUB_CACHE`/`TRANSFORMERS_CACHE`/`TORCH_HOME`/
`UV_CACHE_DIR` under `<data>/.mangaeasy/` — these override an inherited
global value, they are **not** `setdefault`. This is deliberate and was a
real bug: a machine with a global `HF_HOME=D:\hf_cache` / `UV_CACHE_DIR=D:\uv`
(set for other tools) otherwise scattered multi-GB model downloads outside
the install folder, silently breaking the "everything in one folder" promise.
`MANGAEASY_SHARE_CACHES=1` reverts them to `setdefault` for users who
genuinely want a shared cross-project cache. Don't turn these back into plain
`setdefault` without that opt-out. (The non-path vars — telemetry, xet perf,
tokenizers — stay `setdefault`.)

## Packaging (`packaging/`)

`packaging/mangaeasy.spec` + `launcher.py` build a self-contained
distributable via PyInstaller; `make_icon.py`/`icon.ico`/`icon.png` are
packaging assets. See `docs/publishing.md` for the release process
(`scripts/release.py` bumps all three version fields in lockstep; the
release workflow refuses to build if they disagree with the tag).

**Data root for an installed app is per-platform** (fixed in v1.0.0 — the
old "parent of resourcesPath" resolution wrote into %TEMP% on Windows
portable and into read-only mounts on macOS/Linux):

- Windows portable: next to the exe (`PORTABLE_EXECUTABLE_DIR`).
- macOS: `~/Library/Application Support/mangaEasy`.
- Linux: `$XDG_DATA_HOME/mangaEasy` (default `~/.local/share/mangaEasy`).
- Dev checkout: the repo root. `MANGAEASY_ROOT` env var overrides everywhere.

This resolution lives in `_default_frozen_root()` in
`mangaeasy/tools/external.py` (the frozen-CLI authority); `MANGAEASY_ROOT`
overrides it everywhere. Never assume `~/.mangaeasy`.

Core binaries (ffmpeg/ffprobe/uv/git-lfs) are **not bundled** into the
installers — `mangaeasy bootstrap-tools` downloads them on demand (the
Setup tab offers this on first run when doctor reports them missing).

## The machine-readable CLI contract (agents/scripts depend on this)

Added in v1.1.0 and documented in `docs/ai-guide.md` (root `AGENTS.md`
points there). When changing CLI behaviour, keep these stable:

- `commands --json` (catalog from `COMMANDS`), `where --json` (resolved
  paths), `library-list --json` (`mangaeasy/library_scan.py`), and `--json`
  modes on `doctor`/`tools`/`video-check`/`video-validate`/`video-audio-audit`/
  `audio-takes-list`: exactly one JSON object on stdout.
- Marker lines: `MANGAEASY_PROGRESS n/m`, and `MANGAEASY_RESULT {...}` via
  `mangaeasy.utils.emit_result()` as the final line of successful
  generation commands — new generation commands must emit it too.
- Exit codes: 0 ok / 1 runtime failure / 2 usage error. No command may ever
  prompt for interactive input.
- stdout/stderr are forced to UTF-8 in `cli.py` (`_force_utf8_stdio`) —
  don't remove; piped output on Windows is cp1252 otherwise and crashes.
- `mangaeasy mcp` (`mangaeasy/mcp_server.py`) is a stdlib-only MCP stdio
  server whose tools shell out to the CLI; adding a tool means adding an
  entry to its `TOOLS` dict (schema + flag mapping) — no SDK, keep it
  dependency-free. `tests/test_docs_crossref.py` fails if docs mention
  commands that don't exist.

## The agent-flow layer (added post-v1.3.1)

A thin set of commands exists purely so an agent can run the whole
URL→upload flow without hand-holding; their invariants:

- `setup` (`tools/setup.py`) — chains `ensure_core_tools()` + GPU-aware
  `install_tool()` calls. It must stay idempotent/resumable and must keep
  going past individual tool failures (one flaky 33 GB download must not
  waste the others' progress).
- `download --url/--name/--all` — `--url` must keep working without any
  config.json. The complete-chapter fast-skip in `_download_one_chapter()`
  exists so an `--all` re-run doesn't cost one at-home API call per
  already-downloaded chapter — keep it; MangaDex politeness is a feature.
- `style-detect`, `narration-check`, `series-plan` — read-only `--json`
  reporters. `narration-check` is the *structural* half of narration
  verification only; don't grow it into semantic checking — the semantic
  half is `narration-review-sheets` (panel + narration + OCR sheets an
  agent Reads), fed by `panel-transcript` (DeepSeek-OCR 2 over every panel
  into `<item>/transcript.json`, BEFORE narration exists — it grounds
  dialogue paraphrase and speaker attribution; born from viewer feedback:
  wrong speakers, multi-panel summaries on one panel, paraphrase drift).
- `webtoon-cutcheck` — full-resolution review windows around every forced
  auto-split cut / short panel from the ranges manifests; the crop-QA pass
  that replaced judging on downscaled contact sheets (which shipped sliced
  bubbles once). `panels-remap` — after a re-crop, locates archived old
  panels in the stitched strip and carries narration + WAVs to the new
  numbering by interval overlap (dry-run by default; refuses `--apply` on
  orphans). Neither regenerates TTS.
- Agents edit data through the CLI, not by hand-editing JSON:
  `webtoon-override` builds the overrides file (merge indices resolved from
  the manifest's `base` list — the no-override range list webtoon-split now
  records precisely so fixes stay valid across override iterations; indices
  computed by eye shipped one-off merges twice), and `narration-edit`
  upserts/deletes narration/intro entries (name-sorted insertion,
  speakability + image checks on every write, `--prune-audio` deletes the
  stale WAVs). Keep new agent-facing state editable through a command.
- Item renders are freshness-gated: `build_one_chapter()` re-renders when
  panels/narration/audio are newer than the existing item video
  (`stale_reason()`); `--overwrite-video` still forces. Don't restore the
  old unconditional skip-if-exists — it silently joined six stale chapters
  into a "successful" build once. `video-validate` splits intentional
  narration skips / orphan audio into `warnings` (exit 0) vs real breakage
  in `errors`, and its item-WAV duration expectation is frame-aligned
  (matching `build_item_narration_wav`'s apad/atrim) — keep both.
- `series-plan`/`series-mark-published` own `library/<project>/publish.json`
  (machine-managed, like manga.json). Batches are stable fixed windows over
  the sorted item list — don't make them shift when items are added.
- `thumbnail-compose` archives the previous output before overwriting,
  like every other generation command.
- The agent workflow itself lives in `.claude/skills/manga-recap/SKILL.md`
  (auto-loaded by Claude Code; plain runbook for other agents). The docs
  cross-reference test covers it — commands mentioned there must exist.

## YouTube integration (`mangaeasy/youtube/`)

`youtube-auth`/`youtube-status`/`youtube-logout`/`youtube-upload` (v1.2.0).
`store.py` owns the on-disk layout (`<home>/youtube/{client_secret,token,
channel}.json`) with **plain-JSON helpers only** — the google-auth imports
stay inside `auth.py` (lazy-import convention). Upload is hand-rolled
`requests` against the resumable protocol (`upload.py`), not the Google
discovery client; keep it that way — it's what keeps the PyInstaller
bundle small and the deps shallow. Rules: tokens are secrets (print
paths/booleans, never contents); default privacy stays `private` (YouTube
force-locks unaudited API projects to private — documented in
docs/youtube.md, don't "fix" it); `youtube-upload --json` prints its JSON
object as the *last* stdout line (after `MANGAEASY_RESULT`) because the
MCP server parses the final line.

`store.SCOPES` requests **full video management** (`youtube.force-ssl`, on
top of upload + readonly) so a bad take can be deleted/replaced through the
API instead of a manual YouTube Studio trip. It was upload-only originally;
tokens granted back then still upload fine but get 403
`insufficientPermissions` on delete/update — the fix is re-running
`youtube-auth` (re-consent), not code. Scope still excludes comments,
playlists, and account settings; don't broaden it further without need.

## Tests, lint, CI

- `tests/` is a pytest suite for the pipeline's pure logic (item selection,
  narration loading, archive-before-overwrite, shard-aware resume pruning,
  CLI dispatch). Run `uv run pytest` before committing; add a test when
  fixing logic bugs in those areas.
- `uv run ruff check .` must stay clean (config in pyproject.toml —
  correctness rules only, style checks deliberately off).
- `.github/workflows/ci.yml` runs ruff/pytest/compileall on every push/PR
  across all three OSes; `release.yml` additionally smoke-tests the frozen
  backend (`--version`, `doctor --json`) before packaging.

## Conventions worth preserving

- **Lazy imports in `cli.py`** — never import a heavy optional dependency
  (torch, opencv, transformers) at module top level if it's only
  needed by one subcommand; import inside that subcommand's module instead.
- **CPU fallback everywhere** — every pipeline stage must work without a
  GPU (`--device auto|cuda|cpu`, encoder auto-detection preferring
  hardware encoders but always falling back to `libx264`). Don't add a
  GPU-only code path without a CPU equivalent.
- **dB units for any new audio-volume control** — match the existing
  `music_volume_db` / `--music-volume-db` convention, not a linear
  multiplier.
- **Archive, don't delete, generated output** before overwriting it (see
  above).
- **`load_narration()` is the only narration reader** — never re-parse
  `narration.json` directly in a new module.
- Git commits only happen when the user explicitly asks; this has been
  reiterated multiple times in this project's history — don't commit
  proactively after a fix, even if tests pass.
