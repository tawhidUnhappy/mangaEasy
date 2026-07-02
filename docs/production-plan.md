# mangaEasy — Production-Readiness Plan

> **Status (2026-07-02): executed for v1.0.0.** Phases 0-1 fully done;
> Phase 2/3 done except per-OS manual QA passes and code signing (owner
> chose to ship unsigned; SmartScreen/Gatekeeper steps are documented on
> the release page and in install.md). Data-root decisions: OS-standard
> locations, first-run ffmpeg download. See CHANGELOG.md for the shipped
> summary. This file is kept as the working checklist for what remains
> (manual QA matrix, signing if ever funded, Flatpak, auto-update).

Goal: make mangaEasy a solid, comfortable, install-and-use desktop app on
Windows / macOS / Linux — the way VS Code ships — while keeping its core
promise: **isolated and standalone** (no system Python/Node/ffmpeg needed,
all app data in one known place, deleting it leaves nothing behind).

Current state (audited 2026-07-02, v0.9.30):

- The release pipeline already exists and passes: pushing a `v*` tag builds
  Windows portable exe, macOS dmg+zip, Linux AppImage+deb+tar.gz and
  publishes them to the GitHub Releases page.
- BUT the installed app is broken or self-contradictory on **every** shipped
  format because of how the data root is resolved (P1.1 below), the
  "bundled ffmpeg" claim is false (P1.2), and every release's assets are
  mislabeled `0.1.0` (P1.3).
- There are **zero automated tests** and no CI on push/PR — only the tag
  build. Nothing guards against regressions.

The plan is five phases. Each item is a checkbox so progress is trackable.
Phases 1–2 are the "make it actually work" core; 3 is the VS Code-level
polish; 4 is cleanup; 5 is the ship sequence the user asked for
(clean → push → tag → GitHub Actions builds → Releases page).

---

## Phase 0 — Safety net first (CI + tests)

Nothing below can be done confidently without a regression net.

- [ ] **0.1 CI workflow on every push/PR** (`.github/workflows/ci.yml`):
      3-OS matrix; `uv sync`; `python -m compileall mangaeasy`;
      `uv run mangaeasy --version`; `ruff check`; desktop `npm ci`,
      `npm run typecheck`, `npm run lint`, `electron-vite build`.
- [ ] **0.2 Adopt ruff** (config in `pyproject.toml`) and fix what it finds.
- [ ] **0.3 pytest suite for pure logic** (no GPU/network needed):
      `expand_item_tokens`/`merge_item_selection`, `load_narration` +
      `intro.json` prepend, `archive_before_overwrite`, resume-pruning
      shard math (`prune_recent_audio_for_resume`), `frame_aligned_duration`,
      CLI dispatch + unknown-command suggestions, config read/write
      round-trip, `ProgressParser` (port to a testable form or test via
      vitest on the TS side).
- [ ] **0.4 Packaged-app smoke test in CI**: after PyInstaller build, run
      `dist/mangaEasy/mangaeasy --version` and `doctor --json`; after
      electron-builder, launch the app headless (xvfb on Linux) with a
      `--smoke-test` flag that opens the window, runs one backend call
      (`doctor`), and exits 0. This one test would have caught most of
      Phase 1.

## Phase 1 — Critical: the shipped app must actually work (isolation core)

- [ ] **1.1 Per-platform writable data root** — the single biggest defect.
      `appRoot()` (desktop/src/main/paths.ts) and Python `app_root()`
      (mangaeasy/tools/external.py) resolve to `dirname(resourcesPath)` /
      `dirname(sys.executable)` when packaged. That is:
      - **Windows portable exe**: a random `%TEMP%` extraction dir — the
        multi-GB `.mangaeasy/` (tool envs, models, vendored binaries) lands
        in temp and is lost every launch; nothing persists next to the exe.
        Fix: use `process.env.PORTABLE_EXECUTABLE_DIR`.
      - **macOS**: inside the read-only, signature-sealed (and, when
        quarantined, translocated) `.app` bundle. Fix: default to
        `~/Library/Application Support/mangaEasy`.
      - **Linux AppImage**: inside the read-only squashfs mount. Fix: dir
        next to the AppImage file (`process.env.APPIMAGE`) or XDG data home.
      - **Linux deb**: root-owned `/opt/mangaEasy`. Fix: XDG data home
        (`~/.local/share/mangaEasy`).
      Implementation: resolve once in Electron main (it already exports
      `MANGAEASY_ROOT`/`MANGAEASY_HOME` to every child, so the Python side
      inherits the fix), but also fix the Python frozen fallback for
      CLI-only use. Add a **first-run "data folder" screen** showing the
      per-platform default with a Change… button, persisted; a "Reveal data
      folder" and "Delete everything" affordance in the app keeps the
      isolation promise honest on platforms where data can't live next to
      the app. Update install.md / release-notes wording per platform.
- [ ] **1.2 Actually ship (or actually fetch) ffmpeg/uv/git-lfs.** CI runs
      `mangaeasy bootstrap-tools`, which writes `<repo>/.mangaeasy/tools/_vendor`
      — and then **nothing bundles it**: PyInstaller datas and
      electron-builder extraResources don't include it, so the released app
      contains no ffmpeg despite install.md claiming it's bundled. Choose:
      (a) bundle `_vendor` via extraResources (+~100–200 MB per installer), or
      (b) drop the dead CI step and make the app self-bootstrap on first run
      with a visible progress UI and a clear offline-error path.
      Recommendation: (b) for size, plus cache-friendly retry. Either way
      fix the false claims in install.md and the release body text.
- [ ] **1.3 One version everywhere.** pyproject/`__init__.py` say 0.9.30;
      `desktop/package.json` says 0.1.0, so **every release's assets are
      named `mangaeasy-desktop-0.1.0-*`** regardless of tag. Fix: in the
      release workflow (and a local `scripts/set-version`), write the tag
      version into desktop/package.json before electron-builder, and fail
      the build if tag ≠ pyproject ≠ `__init__.py`. Also unify artifact
      naming — the mac zip comes out as `mangaEasy-…-mac.zip` while
      everything else is `mangaeasy-desktop-…` (set one `artifactName`
      convention, e.g. `mangaEasy-<version>-<os>-<arch>.<ext>`).
- [ ] **1.4 macOS ffmpeg story**: no static build is vendored on macOS and
      install.md tells users to `brew install ffmpeg` — but 1.1's brew may
      not be on PATH for a GUI-launched app. Vendor a maintained static
      macOS ffmpeg (or build an LGPL one in CI), else surface the brew
      requirement inside the Setup tab with a copyable command and a
      re-check button, not only in docs.
- [ ] **1.5 Fix POSIX dev fallback** in paths.ts (`.venv/Scripts/python.exe`
      is Windows-only; also try `.venv/bin/python`).
- [ ] **1.6 Editor launch robustness** (ipc-handlers.ts): 15 s URL timeout
      rejects but leaves the detached Flask process running; PyInstaller
      cold start + antivirus scan can easily exceed 15 s on first launch.
      Raise/spinner it, kill the child on timeout, offer retry.
- [ ] **1.7 Harden JSON IPC**: `JSON.parse(stdout)` on `doctor`,
      `audio-takes-list` breaks on any stray print/warning. Delimit the JSON
      (e.g. sentinel line) or write to a temp file the TS side reads.
- [ ] **1.8 node-pty in packaged builds**: `npmRebuild: false` with a native
      module is only safe if prebuilds match Electron's ABI on all three
      OSes — verify in the packaged smoke test (0.4); if broken anywhere,
      enable rebuild or pin matching prebuilds.

## Phase 2 — Solid & comfortable (bugs, confusion, first-run UX)

- [ ] **2.1 Structured QA pass** of every tab (Setup, Workflow, Batch,
      Project, Editor) on all three OSes using the *packaged* app, with a
      written checklist per tab; file every bug found as a GitHub issue and
      burn the list down. (Known confusion candidates: two overlapping
      command families; Batch tab's many disabled controls need clearer
      "why"; Project tab raw-JSON vs structured edits.)
- [ ] **2.2 First-run wizard**: data folder (1.1) → core tools bootstrap
      (1.2) → optional AI tool installs with size warnings (Kokoro ~n GB,
      IndexTTS needs NVIDIA GPU) → open/create first project. Today the app
      drops the user into tabs with no guidance.
- [ ] **2.3 Command-surface cleanup**: mark the legacy chapter-era commands
      as "legacy" in `--help` grouping and hide them from the desktop UI;
      one obvious path for new users (the item pipeline).
- [ ] **2.4 Actionable errors**: every failed job should end with a short
      human-readable cause line in the UI (missing narration.json, no GPU,
      offline, ffmpeg missing), not just a raw traceback in the terminal
      pane. Backend: consistent exit codes + final `ERROR: <reason>` line;
      frontend: surface it in the job status.
- [ ] **2.5 Crash & log hygiene**: main-process + backend logs to
      `<data>/logs/` with rotation; React error boundary; "Open logs
      folder" button; uncaught-exception handler that shows a dialog
      instead of dying silently.
- [ ] **2.6 Update notifications**: `doctor --check-updates` exists — check
      GitHub Releases on launch (throttled, offline-safe), show a
      non-nagging "new version" banner linking to the Releases page.
      (Full electron-updater auto-update is optional later; portable exe
      can't hot-update anyway.)
- [ ] **2.7 Process lifetime**: verify `before-quit` really kills PTY
      children + detached Flask editors + GPU worker processes on all
      OSes (tree-kill semantics differ on Windows); no orphaned
      `mangaeasy.exe` after closing the window mid-job.
- [ ] **2.8 Path robustness**: spaces, non-ASCII manga names, Windows long
      paths (>260 chars: library/<name>/<item>/panels/<long file>.png)
      through ffmpeg/TTS end-to-end; add tests.
- [ ] **2.9 Honest GPU claims**: release notes promise CUDA *and* Apple MPS
      auto-config; doctor only detects NVIDIA. Either implement
      MPS detection/route Kokoro to MPS on Apple Silicon, or soften the
      claim.
- [ ] **2.10 Config editing safety**: Project tab keeps raw-JSON and
      structured state in sync via `updateSystemConfig` — add a unit test
      and JSON-schema validation with inline errors so hand-edits can't
      silently corrupt `config.system.json`.

## Phase 3 — Distribution polish (the VS Code bar)

- [ ] **3.1 Code signing / notarization** (needs owner accounts+secrets):
      - macOS: Developer ID cert + notarization in CI (`notarize: true`).
        Until then: ad-hoc sign so the app isn't "damaged", and document
        right-click-Open / `xattr -cr` prominently on the release page.
      - Windows: Authenticode cert (or accept SmartScreen "More info → Run
        anyway" and document it).
- [ ] **3.2 macOS Intel support**: current build is arm64-only
      (macos-latest). Add an x64 build (macos-13 runner) or a universal
      binary; PyInstaller backend must match arch.
- [ ] **3.3 electron-builder config fixes**: replace placeholder
      `publish.url: https://example.com/auto-updates` (point at GitHub or
      remove); fix `maintainer: electronjs.org` in the deb; proper desktop
      categories (AudioVideo;Video;), full icon set.
- [ ] **3.4 Licenses & attribution**: shipping/downloading **GPL ffmpeg**
      (BtbN gpl builds) creates notice obligations — switch to LGPL builds
      or ship license texts; add a NOTICE/third-party-licenses file and an
      About dialog (version, licenses, data-folder path, link to logs).
- [ ] **3.5 End-user README**: rewrite top of README for users (what it
      does, screenshots/GIF, download links, 5-minute quickstart);
      contributor/dev material moves below or into docs/. Remove internal
      references (AiSongTool comments, NiceGUI-era comparisons) from code
      comments while touching those files.
- [ ] **3.6 One-command release script** (`scripts/release.py` or make
      target): bump all versions in lockstep, update changelog, commit,
      tag, push — replaces the manual 4-step checklist in
      docs/publishing.md (keep the doc, have it document the script).
- [ ] **3.7 CHANGELOG.md** maintained per release; release workflow copies
      the entry into the release body instead of the current static
      (and currently inaccurate) boilerplate.

## Phase 4 — Cleanup (repo and disk)

The git repo is already clean (188 tracked files; user data, venvs, tool
envs, and build output are all gitignored). Cleanup is mostly local-disk
and dead-weight removal:

- [ ] **4.1 Delete untracked build leftovers**: `desktop/out/`,
      `desktop/dist/`, `dist/`, `build-tmp/`, `__pycache__/`,
      `desktop/resources/backend/` (rebuilt by CI), stale `work/` scratch.
      **Never** auto-delete `library/`, `.mangaeasy/`, `config*.json` —
      user data; confirm anything ambiguous with the owner first.
- [ ] **4.2 Remove dead code/steps**: the CI `bootstrap-tools` step if 1.2
      chooses self-bootstrap; `packaging/make_icon.py` if icons are final;
      any legacy command whose module nothing and no one uses (audit
      before removing — the CLAUDE.md warns the legacy family is still
      used for single-chapter workflows).
- [ ] **4.3 Docs truth pass**: install.md, publishing.md, app.md,
      architecture.md re-read against post-Phase-1 reality (data-dir story,
      ffmpeg story, version story, macOS caveats).

## Phase 5 — Ship it

- [ ] **5.1 Full pre-flight**: CI green on all matrices; packaged smoke
      tests green; fresh-environment install test on each OS (clean VM or
      new user account): download the artifact → install/run → first-run
      wizard → bootstrap → render the sample project → delete data folder →
      confirm nothing left behind.
- [ ] **5.2 Version bump to `v1.0.0`** (all three files in lockstep via the
      release script) + changelog entry describing the production release.
- [ ] **5.3 Commit and push `main`** to
      https://github.com/tawhidUnhappy/mangaEasy (only with the owner's
      go-ahead, per project convention).
- [ ] **5.4 Push the tag `v1.0.0`** — this *is* the GitHub build trigger:
      `.github/workflows/release.yml` builds Windows/macOS/Linux and
      publishes all artifacts to the Releases page automatically.
- [ ] **5.5 Verify the release**: all expected assets present, correctly
      versioned names, download one per platform and spot-check; Releases
      page is the public download link.

---

## Decisions needed from the owner

1. **Data-folder strategy** (1.1): accept per-platform OS-standard data dirs
   (recommended; with in-app "reveal"/"delete everything" buttons), or
   insist on strictly app-adjacent data where the OS allows it?
2. **ffmpeg**: bundle (bigger downloads) or first-run download (needs
   network once)? Recommended: first-run download with progress UI.
3. **Code signing**: willing to pay for Apple Developer ($99/yr) and/or a
   Windows cert? Without them the app still ships but with scary OS
   warnings that must be documented.
4. **v1.0.0** as the production version number?

## Suggested execution order

Phase 0 → 1 are sequential and highest value. 2 and 3 can interleave.
4 runs continuously but has a final sweep before 5. Phase 5 is one sitting
once everything is green.
