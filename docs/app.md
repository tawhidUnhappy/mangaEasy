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

## Tabs

### Setup

- Prerequisite checks: git, git-lfs, uv, uvx, FFmpeg, FFprobe, NVIDIA GPU.
- One card per external AI tool (IndexTTS, MAGI v3, Kokoro) showing
  whether it is installed and where it resolved.
- **Install / Reinstall** buttons run `mangaeasy install-tool` for you, with the
  full output streaming into the log console. Options: *CPU-only* and *Skip
  model download*.

### Project

- Set the **project folder** every command and editor runs against.
- Edit `config.json` (manga URL/ID, name, chapter) as a simple form.
- Edit `config.system.json` as validated JSON (seeded from the bundled example
  on first use). Saving creates the files if they don't exist.

### Run

- **General video pipeline**: pick the step (full pipeline or an individual
  stage), content folder, item range, encoder, and TTS device; toggle
  long-video build and overwrite flags; press **Run**. Logs stream live and
  **Stop** terminates the run.
- **Manga chapter commands**: run `download`, `render-video`, `add-bgm`,
  `join-chapters`, `to-pdf`, etc. against the current project folder, with an
  optional free-form extra-args field.

Only one job runs at a time; the indicator in the top bar shows what's active.

### Editors

One-click launch for the existing web editors (`cut-page`, `panel-editor`,
`narration-editor`, `narration-editor-all`, `narration-review`). Each editor
opens automatically in your browser and can be stopped from the same card.

## Log console

The bottom panel streams everything — installer output, pipeline progress,
editor logs — over server-sent events. It can be cleared or collapsed.
