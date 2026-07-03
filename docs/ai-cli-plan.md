# mangaEasy — AI-Assistant-Friendly CLI Plan

> **Status (2026-07-03): executed in v1.1.0 — all phases A–E, including the
> optional MCP server.** The deliverable doc is `docs/ai-guide.md` (+ root
> `AGENTS.md`). Not done: C3 shipped as a read-only About line (no shim
> installer); desktop app still uses its own TS library scan (noted in E as
> a later unification).

Goal: any AI assistant (Claude Code, Cursor, a plain LLM with a shell tool,
…) can drive mangaEasy end-to-end through the `mangaeasy` CLI without
guessing — discover state, run the pipeline, find the outputs, recover from
errors — documented in one authoritative `.md` file. The isolated,
self-contained install story stays exactly as it is (per-platform data root,
`MANGAEASY_ROOT`/`MANGAEASY_HOME` overrides, nothing written outside it).

What already works for agents (audited 2026-07-03, v1.0.0):

- One CLI (`mangaeasy <command>`), ~50 subcommands, every command has
  `--help`, **no command ever prompts for interactive input**.
- `doctor --json`, `audio-takes-list --json` already machine-readable;
  `MANGAEASY_PROGRESS n/m` marker lines already exist for progress parsing.
- Isolation env contract (`MANGAEASY_ROOT`, `MANGAEASY_HOME`,
  `MANGAEASY_TOOLS_DIR`) already lets a CLI process share the GUI app's data.

What's missing: a machine-readable command catalog, `--json` on the
inspection commands, any CLI way to list projects/chapters (today only the
GUI can), a stable machine-parsable "here is the output file" line, a
documented exit-code contract, and above all **the guide document itself**.

---

## Phase A — Machine-readable CLI contract

- [ ] **A1 `mangaeasy commands --json`** — full command catalog: for every
      entry in `cli.py`'s `COMMANDS` dict emit
      `{name, group, help, usage: "mangaeasy <name> --help"}`. Static data
      only (no heavy imports — the lazy-import design must survive); each
      command's own `--help` stays the source of truth for flags. Also
      `mangaeasy commands` (human table, same data).
- [ ] **A2 `mangaeasy where --json`** — resolved paths + environment:
      version, app_root, mangaeasy_home, tools_home, vendored bin dirs,
      frozen-or-not, platform. This is the first command an agent should run;
      it answers "where is everything on THIS machine" without guessing.
- [ ] **A3 `--json` on the inspection commands** — `video-check`,
      `video-validate`, `video-audio-audit`, `tools`. Same information the
      human output has, as one JSON object on stdout (single line, so it
      survives mixed output). Human format unchanged when the flag is absent.
- [ ] **A4 `mangaeasy library-list [--json]`** — the state-discovery gap:
      list projects under a `--project-root`'s `library/` and, per item,
      what exists (panels count, narration.json present, audio files count,
      rendered video present). Reuses the same logic the desktop's
      `config.ts` implements in TypeScript today (port it to Python; the
      desktop can later switch to calling this command so there is one
      implementation).
- [ ] **A5 Exit-code contract** — audit + document: `0` success, `1` runtime
      failure (bad input data, tool missing, generation error), `2` CLI
      usage error (argparse already does this). Fix any command found
      returning something else. Add a test that spot-checks representative
      commands.
- [ ] **A6 Stable result marker** — generation commands (`video`,
      `video-render`, `video-join`, `video-add-bgm`,
      `video-normalize-audio`) end their successful run with one line:
      `MANGAEASY_RESULT {"outputs": ["<abs path>", …]}` — same spirit as the
      existing `MANGAEASY_PROGRESS` and `MANGAEASY_OPEN_URL` markers. Agents
      stop scraping human log text; the desktop app can adopt it later too.
- [ ] **A7 Plain-pipe friendliness check** — verify important output is
      line-oriented and readable when stdout is a pipe, not a PTY (agents
      use plain pipes): tqdm already degrades on non-TTY; confirm no ANSI
      color is forced; document that `\r`-progress may appear and is safe to
      ignore in favor of `MANGAEASY_PROGRESS` lines.

## Phase B — The AI usage document (the core deliverable)

- [ ] **B1 `docs/ai-guide.md`** — the complete, single-file reference an AI
      (or a human) needs to operate mangaEasy:
      1. What mangaEasy does (one paragraph + the item/project data model).
      2. **Getting a working `mangaeasy` command**, three supported modes,
         all isolation-preserving:
         - `uv tool install git+https://github.com/tawhidUnhappy/mangaEasy.git`
           (recommended for agent environments);
         - a source checkout (`uv sync`, `uv run mangaeasy …`);
         - the installed desktop app's bundled backend binary — exact path
           per platform (macOS `mangaEasy.app/Contents/Resources/backend/mangaeasy`,
           Linux `/opt/mangaEasy/resources/backend/mangaeasy` or inside the
           extracted tar.gz; Windows portable: not stable — use the first two
           modes) — plus how to point it at the user's existing GUI data with
           `MANGAEASY_ROOT` so agent and GUI share projects and tools.
      3. First-run: `where --json`, `doctor --json`, `bootstrap-tools`,
         `install-tool kokoro-82m` (+ size/network warnings).
      4. Project anatomy: `library/<project>/<item>/panels/` +
         `narration.json` schema (with `intro.json`, `ocr` fields), where
         generated audio/video/work output goes, archive-before-overwrite
         (`old/run_NNNN/`), what is safe to delete (`video-clean-*`).
      5. Command reference by group (generated from A1's catalog) with the
         5–6 flags per command that matter in practice.
      6. **Recipes** — copy-paste sequences for the common jobs: images
         folder → narrated video; batch chapters → one long video with BGM;
         re-mix BGM only; resume an interrupted audio run; validate before a
         long build; restore a previous audio take.
      7. Machine-output contract: `--json` commands with example payloads,
         `MANGAEASY_PROGRESS` / `MANGAEASY_RESULT` line formats, exit codes.
      8. Environment variables (`MANGAEASY_ROOT`, `MANGAEASY_HOME`,
         `MANGAEASY_TOOLS_DIR`, `PROJECT_ROOT`/`AUDIO_ROOT`/…, `HF_HUB_OFFLINE`
         behavior).
      9. Limits & gotchas an agent must respect: `--gpu-workers` ceiling
         (4 on an RTX 3060-class card), never edit `narration.backup.json`,
         never delete inside `library/`, dB-native volume flags, CPU
         fallback flags.
      10. Troubleshooting table: symptom → likely cause → command to run.
- [ ] **B2 `AGENTS.md` at the repo root** — the emerging cross-tool
      convention file agents auto-discover. Short: "this project's tool
      surface is the `mangaeasy` CLI; read docs/ai-guide.md; run
      `mangaeasy commands --json` and `mangaeasy where --json` first; repo
      development conventions live in CLAUDE.md."
- [ ] **B3 Wire-up** — link the guide from README ("Using mangaEasy with an
      AI assistant" section), ship `docs/` in the sdist (already included),
      mention in the next release notes.

## Phase C — Access from the installed app, isolation intact

- [ ] **C1** Document (in the guide) that the packaged backend *is* the full
      CLI and how to invoke it directly on macOS/Linux installs; explicitly
      recommend `uv tool install` on Windows (the portable exe's backend
      lives in a temp extraction dir — no stable path).
- [ ] **C2** Show the isolation-preserving bridge: agent sets
      `MANGAEASY_ROOT=<the GUI app's data folder>` (the path shown in the
      app's Setup → About) and then shares the same installed tools, config,
      and projects as the GUI — zero duplicate downloads, still nothing
      outside that folder.
- [ ] **C3 (nice-to-have)** Setup → About gains a "CLI access" line showing
      the exact command an agent should use on this machine (copy button) —
      generated from the same logic as `where --json`.

## Phase D — Verify like an agent would

- [ ] **D1** pytest coverage for A1–A4 and A6 (catalog shape, `where` keys,
      `library-list` against a tmp fixture project, `MANGAEASY_RESULT`
      emitted and parseable, exit codes).
- [ ] **D2** End-to-end agent-style smoke test, plain pipes, no PTY, no ML
      models: build a tiny fixture project (2 generated images +
      narration.json + 2 ffmpeg-generated silent WAVs), then
      `video-check --json` → `video-render` → assert the item video exists
      and `MANGAEASY_RESULT` names it. Runs in CI (ffmpeg fetched via
      `bootstrap-tools`, cached).
- [ ] **D3** Doc truth check: every command/flag mentioned in ai-guide.md
      exists (`commands --json` cross-reference test — docs can't rot
      silently).
- [ ] **D4** Update CLAUDE.md (new commands + the "one narration reader"
      style conventions apply to `library-list`), CHANGELOG entry, version
      bump (v1.1.0), push, tag → GitHub release.

## Phase E — Later / optional (not in this pass)

- MCP server (`mangaeasy mcp`) exposing check/render/join/status as typed
  MCP tools over stdio — the natural next step once the JSON contract from
  Phase A exists; agents that speak MCP get schema'd tools for free.
- Desktop app consuming `library-list --json`/`MANGAEASY_RESULT` instead of
  its own TS re-implementations (single source of truth).

## Order & size

A (contract) → B (guide) → C (docs-mostly) → D (verify+ship). A and B are
the bulk; C is mostly writing; D closes the loop. Everything lands in one
minor release (v1.1.0) — no breaking changes, all additions.
