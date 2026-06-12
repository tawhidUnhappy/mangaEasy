# The Desktop App

```bash
mangaeasy app
```

Opens the mangaEasy control center in a native desktop window (pywebview). The
window wraps a local web UI served on `127.0.0.1:5010`; nothing is exposed to
the network.

Options:

```bash
mangaeasy app --browser     # open in your default browser instead of a window
mangaeasy app --port 5050   # use a different local port
```

On Linux/macOS, pywebview may need a system GTK/Qt backend; if no GUI backend is
available the app automatically falls back to the browser.

## Choosing folders

Every folder field (project folder, manga folder, output folder) has a
**Browse…** button:

- In the desktop window it opens the **native OS folder dialog**.
- In browser mode it opens a small **in-app folder browser** (drives, home
  shortcut, click-to-enter folders).

Next to each field, **Open** shows the folder in your file manager — handy for
finding the finished videos.

Folder choices and run options are **remembered between launches** in
`~/.mangaeasy/app_state.json`. On start the app also reuses your last project
folder when the current directory doesn't look like a project.

## Tabs

### 1 · Setup

- Prerequisite checks: git, git-lfs, uv, uvx, FFmpeg, FFprobe, NVIDIA GPU.
- One card per external AI tool (IndexTTS, MAGI v3, Kokoro) showing
  whether it is installed and where it resolved.
- **Install / Reinstall** buttons run `mangaeasy install-tool` for you, with the
  full output streaming into the log console. Options: *CPU-only* and *Skip
  model download*.

### 2 · Project

- Pick the **project folder** every command and editor runs against
  (Browse… applies it immediately).
- Edit the manga settings (`config.json`: manga URL/ID, name, chapter) as a
  simple form.
- **Music & voice** — pick the background music file and the voice reference
  WAV (the voice IndexTTS clones for narration) with file dialogs. Files
  inside the project folder are stored as portable relative paths. Used by
  `index-tts`, `add-bgm`, `join-chapters` and the long-video build.
- `config.system.json` lives under **Advanced** as validated JSON (seeded from
  the bundled example on first use). Saving creates the files if they don't
  exist.

### 3 · Create videos

Three numbered steps:

1. **Choose folders** — the *manga folder* (one subfolder per item/chapter,
   each with `panels/` + `narration.json`) and the *output folder* where
   finished videos land. Relative names like `content` resolve inside the
   project folder; Browse… picks any folder on the machine.
2. **What to do** — pick the step (everything, or a single stage) and the
   voice engine. **YouTube loudness (−14 LUFS)** runs a two-pass loudness
   normalization on the finished long video so it plays at YouTube's
   standard volume; it is also available as a standalone step. Encoder,
   TTS device, item ranges and overwrite flags sit under
   **Advanced options**.
3. **Run** — Start / Stop with live logs. The status line tells you when the
   job finishes.

Below that, **Manga chapter tools** run the classic per-chapter commands
(`download`, `render-video`, `add-bgm`, `join-chapters`, `to-pdf`, …) against
the current project folder, with an optional free-form extra-args field.

Only one job runs at a time; the indicator in the top bar shows what's active.

### Editors

One-click launch for the existing web editors (`cut-page`, `panel-editor`,
`narration-editor`, `narration-editor-all`, `narration-review`). Each editor
opens automatically in your browser and can be stopped from the same card.

## Log console

The bottom panel streams everything — installer output, pipeline progress,
editor logs — over server-sent events. It can be cleared or collapsed.
