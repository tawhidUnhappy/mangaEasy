# mangaEasy — Complete guide for AI assistants (and scripts)

This is the one document an AI assistant needs to operate mangaEasy from a
shell. Everything here is also true for human scripting. Repo *development*
conventions live in `CLAUDE.md`; this file is about **using** the tool.

mangaEasy turns folders of images + a narration script into narrated videos:
per-chapter ("item") videos, optionally joined into one long video with
background music, ready for YouTube. Everything is driven by one command:
`mangaeasy <subcommand>`. No command ever prompts for interactive input.

**Orient yourself with two calls** (do this first on any machine):

```bash
mangaeasy where --json      # where this install keeps data/tools + version
mangaeasy commands --json   # the full command catalog
```

Prefer MCP? `mangaeasy mcp` runs an MCP stdio server with typed tools —
see [MCP server](#mcp-server) below. The CLI and MCP expose the same engine.

---

## 1. Getting a working `mangaeasy` command

All three modes keep the isolation promise: everything mangaEasy writes
stays under one data folder ("app root"), never scattered over the system.

### Mode 1 — uv tool install (recommended for agent environments)

```bash
uv tool install git+https://github.com/tawhidUnhappy/mangaEasy.git
mangaeasy --version
```

### Mode 2 — source checkout

```bash
git clone https://github.com/tawhidUnhappy/mangaEasy.git && cd mangaEasy
uv sync
uv run mangaeasy --version
```

### Mode 3 — the user's installed desktop app (share its data!)

The desktop app bundles the full CLI as its backend binary:

| Platform | Backend CLI path |
|---|---|
| macOS | `/Applications/mangaEasy.app/Contents/Resources/backend/mangaeasy` |
| Linux (deb) | `/opt/mangaEasy/resources/backend/mangaeasy` |
| Linux (tar.gz) | `<extracted>/resources/backend/mangaeasy` |
| Windows (portable) | not stable (self-extracts to temp) — use Mode 1/2 instead |

The app's Setup → About shows its data folder. To operate on the **same
projects and installed tools** as the user's GUI, set `MANGAEASY_ROOT` to
that folder before running any command:

```bash
MANGAEASY_ROOT="$HOME/Library/Application Support/mangaEasy" \
  /Applications/mangaEasy.app/Contents/Resources/backend/mangaeasy library-list \
  --project-root "$HOME/Library/Application Support/mangaEasy" --json
```

Default data folders: Windows portable = next to the exe; macOS =
`~/Library/Application Support/mangaEasy`; Linux = `~/.local/share/mangaEasy`.

## 2. First-run setup

```bash
mangaeasy doctor --json          # ffmpeg/git/GPU/tool status
mangaeasy bootstrap-tools        # one-time ~100 MB: ffmpeg/ffprobe/uv/git-lfs
                                 #   downloaded into the data folder, not system-wide
mangaeasy install-tool kokoro-82m  # the CPU-friendly TTS voice (~1-2 GB, one-time)
```

`doctor --json` fields that matter: `executables.ffmpeg`/`ffprobe`
(null = missing → run `bootstrap-tools`), `gpu_backend` (`cuda`/`mps`/`cpu`),
`tools` (installed AI tools). Optional bigger tools: `index-tts` (voice
cloning, needs NVIDIA GPU), `magi-v3` (panel detection), `got-ocr2` (OCR).
All installs are **long-running downloads** — expect minutes, stream the
output.

## 3. Project anatomy

A *project* is a folder of numbered *items* (chapters) under a project root:

```
<project-root>/library/<project-name>/
  01/
    panels/              panel images (.png/.jpg/.webp), one per narration entry
    narration.json       [{"image": "chapter1_001.png", "narration": "text..."}, ...]
    intro.json           OPTIONAL, same shape — prepended at load time (cold open)
  02/ ...
```

Generated output goes to separate roots you pass explicitly (recommended
for agents — never rely on cwd defaults):

- `--audio-root <dir>` → `<dir>/<project>/<item>/<panel>.wav` per-panel narration
- `--output-root <dir>` → `<dir>/<project>/items/item_<NN>.mp4` per-item videos
  and `<dir>/<project>/<project>_full_<timestamp>.mp4` joined long videos
- `--work-dir <dir>` → scratch, safe to delete (`video-clean-work`)

Item selection everywhere: `--items 01 02 05-08` or `--item-range 01-12`.

**Safety rules an agent must follow:**

- Never create/delete/rename files inside `library/` items except
  `narration.json`/`intro.json` content edits the user asked for.
  `narration.backup.json` is machine-managed — do not touch.
- Generated output is archived (`old/run_NNNN/`), never overwritten
  silently; use `video-clean-*` commands to clear it, never raw deletes.
- Volume flags are dB-native (negative = quieter), e.g.
  `--music-volume-db -25`.
- `--gpu-workers` above 4 is known to crash consumer NVIDIA cards; default
  is safe.
- Everything works CPU-only; GPU is an optimization, not a requirement.

## 4. Machine-output contract

- **Exit codes**: `0` success · `1` runtime failure (bad inputs, missing
  tool, generation error — stderr/stdout has the reason, possibly as a
  traceback) · `2` usage error (bad flags; argparse message on stderr).
- **`--json` commands** print exactly one JSON object on stdout:
  `commands`, `where`, `doctor`, `tools`, `library-list`, `video-check`,
  `video-validate`, `video-audio-audit`, `audio-takes-list`. Check the
  `ok` field where present.
- **Marker lines** inside human output (grep for them, ignore the rest):
  - `MANGAEASY_PROGRESS <n>/<total> [label]` — progress ticks.
  - `MANGAEASY_RESULT {"outputs": ["<abs path>", ...]}` — final line of a
    successful generation command (`video`, `video-render`, `video-join`,
    `video-add-bgm`, `video-normalize-audio`); tells you exactly what was
    produced.
- Output is UTF-8 on every platform, including piped stdout on Windows.
- Long-running commands stream plain log lines; `\r`-style progress
  redraws may appear when a TTY is attached — safe to ignore in pipes.

## 5. Command reference (groups)

Run `mangaeasy commands --json` for the always-current list and
`mangaeasy <command> --help` for flags. Highlights per group:

**Setup & app** — `where`, `commands`, `doctor`, `bootstrap-tools`,
`install-tool <name>`, `tools`, `library-list`, `mcp`, `app` (GUI —
don't launch from agents).

**Video pipeline (the recommended workflow)** —
`video` (all-in-one: audio → render → optional join/normalize/BGM),
`video-audio` (Kokoro TTS), `video-audio-indextts` (IndexTTS, GPU),
`video-render`, `video-join`, `video-normalize-audio`, `video-add-bgm`,
`video-check`, `video-validate`, `video-audio-audit [--fix]`,
`video-fade-audio`, `video-clean-audio|video|work|all`,
`audio-takes-list`, `audio-takes-restore`.

**Manga acquire/narration/render (single-chapter era)** — `download`
(MangaDex), `gutter-split`, `process-panels`, the browser editors
(`narration-editor`, `panel-editor`, `cut-page` — interactive, for humans),
`join-narration`, `normalize-narration`, `clean-narration`,
`backup-narration`, `render-video`, `add-bgm`, `join-chapters`,
`timestamps`, `to-pdf`, `watermark`, `convert-images`, `ai-zip`, and
chapter bookkeeping (`init-chapter`, `increment-chapter`, ...). Prefer the
`video-*` pipeline for new work.

## 6. Recipes

Set once for readability (absolute paths recommended):

```bash
ROOT=/abs/path/to/workspace          # any folder the user chose
PROJ=$ROOT/library/myproject         # items live here: $PROJ/01, $PROJ/02, ...
AUDIO=$ROOT/audio  OUT=$ROOT/output  WORK=$ROOT/work
```

### Images folder → narrated video (one item)

```bash
mkdir -p "$PROJ/01/panels"                # put images into panels/
# write $PROJ/01/narration.json: [{"image": "<file in panels/>", "narration": "..."}]
mangaeasy video-check  --project-root "$PROJ" --audio-root "$AUDIO" --items 01 --json
mangaeasy video-audio  --project-root "$PROJ" --audio-root "$AUDIO" --items 01
mangaeasy video-render --project-root "$PROJ" --audio-root "$AUDIO" \
    --output-root "$OUT" --work-dir "$WORK" --items 01
# → MANGAEASY_RESULT {"outputs": [".../items/item_01.mp4"], ...}
```

### Batch chapters → one long video with background music

```bash
mangaeasy video --project-root "$PROJ" --audio-root "$AUDIO" --output-root "$OUT" \
    --work-dir "$WORK" --item-range 01-12 --tts auto \
    --build-long-video --normalize-audio \
    --background-music /abs/path/music.mp3 --music-volume-db -25
```

### Re-mix only the background music (cheap — no re-render/re-join)

```bash
mangaeasy video-add-bgm --project-root "$PROJ" --output-root "$OUT" \
    --background-music /abs/path/other.mp3 --music-volume-db -22
# writes a NEW timestamped *_bgm_* file; the clean join is untouched
```

### Resume an interrupted audio run

```bash
mangaeasy video-audio --project-root "$PROJ" --audio-root "$AUDIO" \
    --item-range 01-12 --resume
```

### Audit & repair audio before a long build

```bash
mangaeasy video-audio-audit --project-root "$PROJ" --audio-root "$AUDIO" --json
mangaeasy video-audio-audit --project-root "$PROJ" --audio-root "$AUDIO" --fix
mangaeasy video-audio      --project-root "$PROJ" --audio-root "$AUDIO"   # regen deleted ones
```

### Restore a previous audio take instead of regenerating

```bash
mangaeasy audio-takes-list    --project-root "$PROJ" --audio-root "$AUDIO" --json
mangaeasy audio-takes-restore --project-root "$PROJ" --audio-root "$AUDIO" --run run_0003
```

## 7. Environment variables

| Variable | Meaning |
|---|---|
| `MANGAEASY_ROOT` | Override the data root (app root). The desktop app sets this for its children; set it yourself to share the GUI's data. |
| `MANGAEASY_HOME` | Override just the `.mangaeasy` data dir (default `<root>/.mangaeasy`). |
| `MANGAEASY_TOOLS_DIR` | Override where AI tool envs live. |
| `PROJECT_ROOT`, `AUDIO_ROOT`, `OUTPUT_ROOT`, `WORK_DIR` | Defaults for the corresponding `--*-root` flags. Agents should pass explicit flags instead. |
| `KOKORO_ROOT`, `INDEX_TTS_ROOT`, `MAGI_V3_ROOT`, `GOT_OCR2_ROOT` | Point at externally-managed tool envs (rarely needed). |

HF/torch/uv caches are automatically redirected under the data folder —
model downloads never touch `~/.cache`.

## 8. MCP server

```bash
mangaeasy mcp        # newline-delimited JSON-RPC 2.0 over stdio
```

Register: `claude mcp add mangaeasy -- mangaeasy mcp` (or client config
`{"command": "mangaeasy", "args": ["mcp"]}`; add `"env": {"MANGAEASY_ROOT":
"..."}` to share a GUI install's data). Tools: `doctor`, `where`,
`library_list`, `video_check`, `video_validate`, `audio_audit`,
`generate_audio`, `render_videos`, `build_long_video`, `add_bgm`,
`run_full_pipeline`, `bootstrap_tools`, `install_tool`. Tool results are a
JSON text block: `exit_code`, parsed `report` (for `--json` commands),
parsed `result` (the `MANGAEASY_RESULT` payload), and tail `output`.
Generation/install tools block until done — that can be many minutes.

## 9. Troubleshooting

| Symptom | Likely cause | Do |
|---|---|---|
| `ffmpeg`/`ffprobe` null in doctor | core tools not downloaded | `mangaeasy bootstrap-tools` |
| `video-audio` fails to import kokoro | TTS env missing | `mangaeasy install-tool kokoro-82m` |
| `video-render` "have no audio yet" | narration changed since audio was generated | `video-audio` again (skips existing), or `video-audio-audit --fix` first |
| Long join fails validation | items missing audio/video | `video-check --json`, fix reported items |
| Output seems stale/missing | it was archived | look in `old/run_NNNN/` next to the output; `audio-takes-list` for audio |
| GPU crash with many workers | too many CUDA contexts | drop `--gpu-workers` to ≤4 (or omit) |
| Slow first model run | models downloading to the data folder | expected once; offline afterwards |
| `unknown command` | typo | the error suggests near-matches; see `mangaeasy commands` |
