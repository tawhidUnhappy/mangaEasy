# The Desktop App

```bash
mangaeasy app
```

Opens the mangaEasy control center as a real native Electron window (the same
app the Releases page ships) — no browser, no local web server exposed to
anything. Tabs are React views talking to the Python backend purely through
IPC: every "Start"/"Install" button spawns a `mangaeasy <command>` subprocess
and streams its output into the built-in terminal pane at the bottom of the
window.

## Choosing folders

Every folder field (project folder, manga folder, output folder) has a
**Browse…** button:

- Opens the **native OS folder dialog** (Electron's `dialog.showOpenDialog`).

Next to each field, **Open** shows the folder in your file manager — handy for
finding the finished videos.

Folder choices and run options are **remembered between launches** in
`<install folder>/.mangaeasy/app_state.json`. On start the app also reuses your last project
folder when the current directory doesn't look like a project.

## Tabs

### 1 · Setup

- Prerequisite checks: git, git-lfs, uv, ffmpeg, ffprobe, GPU (NVIDIA CUDA or
  Apple Silicon MPS, auto-detected).
- One card per external AI tool (IndexTTS, MAGI v3, DeepSeek-OCR 2, Kokoro, Z-Image Turbo) showing
  whether it is installed and where it resolved.
- **Install** buttons run `mangaeasy install-tool` for you, with the full
  output streaming into the terminal pane. **Check for updates** queries
  whether a newer version is available for each installed tool and turns the
  button into **Update** when one is.

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

### 3 · Make a video (guided workflow)

One chapter from start to finish, top to bottom, with a progress badge on
every step ("12 pages ✓", "no panels yet", …):

1. **Get the chapter** — MangaDex URL/ID, manga name, chapter and language,
   then **Download**. Scraped the pages from another site? Open the download
   folder and drop the images in — later steps don't care where pages came
   from.
2. **Crop into panels** — one click opens the page cutter (manga/manhua) or
   the strip arranger (webtoons) in your browser.
3. **Write the narration** — opens the narration editor; or write
   `narration_XX.json` yourself in the chapter folder
   (`[{"image": …, "narration": …}, …]`).
4. **Generate** — **Generate everything** chains
   `index-tts → fade-audio → render-video → add-bgm` as one job (background
   music is skipped automatically when none is set); *Only audio* / *Only
   video* run the halves separately. The finished MP4 lands in the chapter
   folder.

### Batch videos

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

## Terminal

The bottom panel is a real `xterm.js` terminal that streams everything —
installer output, pipeline progress, editor logs — straight from each
subprocess's stdout/stderr over Electron IPC.
