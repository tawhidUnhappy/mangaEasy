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

Making a full recap video end to end (panel detection, narration writing,
thumbnail, YouTube upload)? Follow `docs/recap-video-playbook.md` — a
step-by-step production recipe verified on a real published video. This
file is the command reference underneath it.

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

### Mode 3 — a frozen release build (no Python needed)

Each GitHub release ships a self-contained frozen `mangaeasy` per OS
(`mangaeasy-<platform>.zip` / `.tar.gz`). Unpack it and run the executable
directly — `mangaEasy is a CLI + MCP tool; there is no GUI`:

```bash
./mangaEasy/mangaeasy --version      # dist folder from the archive
./mangaEasy/mangaeasy commands       # the full command list
```

To point a run at a specific data root (models, tools, projects), set
`MANGAEASY_ROOT` before the command:

```bash
MANGAEASY_ROOT="/data/mangaeasy" ./mangaEasy/mangaeasy library-list \
  --project-root "/data/mangaeasy/library" --json
```

Default data folders when `MANGAEASY_ROOT` is unset: Windows = next to the exe;
macOS = `~/Library/Application Support/mangaEasy`; Linux =
`~/.local/share/mangaEasy`.

## 2. First-run setup

One command provisions everything (GPU-aware; see [setup.md](setup.md)):

```bash
mangaeasy setup                  # core binaries + AI tool envs + models
                                 #   --minimal / --all / --skip <tool> / --dry-run
```

Or piece by piece:

```bash
mangaeasy doctor --json          # ffmpeg/git/GPU/tool status
mangaeasy bootstrap-tools        # one-time ~100 MB: ffmpeg/ffprobe/uv/git-lfs
                                 #   downloaded into the data folder, not system-wide
mangaeasy install-tool index-tts   # default full-pipeline TTS voice clone (large, one-time)
mangaeasy install-tool kokoro-82m  # optional CPU-friendly fallback voice (~1-2 GB, one-time)
```

`doctor --json` fields that matter: `executables.ffmpeg`/`ffprobe`
(null = missing → run `bootstrap-tools`), `gpu_backend` (`cuda`/`mps`/`cpu`
— the *machine's* capability, which is what installs and engine selection key
on; the main env deliberately has no torch), `tools` (installed AI tools).
Optional bigger tools: `magi-v3` (panel detection), `deepseek-ocr2` (OCR),
`z-image-turbo` (text-to-image generation, ~33 GB). `index-tts` is the
default full-pipeline TTS engine and is also a long-running download. All
model/tool installs can take minutes, so stream the output.

## 3. Project anatomy

A *project* is a folder of numbered *items* (chapters) under a project root:

```
<project-root>/library/<project-name>/
  manga.json           machine-managed source record (written by `download`):
                       source site, title URL, manga_id, canonical title,
                       per-chapter download info — read this when you need
                       the manga's link or official title
  01/
    panels/              panel images (.png/.jpg/.webp), one per narration entry
    narration.json       [{"image": "chapter1_001.png", "narration": "text..."}, ...]
    intro.json           OPTIONAL, same shape — prepended at load time (cold open)
  02/ ...
```

`mangaeasy library-list --json` includes each project's `manga.json` as a
`manga` field (`null` when absent), so you don't need to read the file
yourself when scanning.

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
  `narration.backup.json` and the project-level `manga.json` are
  machine-managed — read them freely, don't hand-edit them.
- Generated output is archived (`old/run_NNNN/`), never overwritten
  silently; use `video-clean-*` commands to clear it, never raw deletes.
- Volume flags are dB-native (negative = quieter), e.g.
  `--music-volume-db -22`. The value is how far the music sits *below the
  narration*: mixing never attenuates the narration track, so a long video
  normalized to −14 LUFS stays at −14 LUFS after `video-add-bgm`. The music
  stem is loudness-aligned to the same −14 LUFS reference before the offset
  (disable with `--no-music-loudnorm`), so the number is a true LU
  separation regardless of the track's mastering. For narration-driven
  recap videos (dense, wall-to-wall narration) the researched value is
  **−22 (the default)**; sparser voiceover sits at −18 to −20;
  below −25 the bed is inaudible on phone speakers, above −15 it masks the
  voice. `video-add-bgm` also, by default, compresses the bed's own dynamic
  range so it sits at a consistent level (not swelling on its own), carves
  the 2–5 kHz vocal band, and sidechain-ducks the music under the voice —
  the pro voiceover chain. Opt out per-stage with `--no-condition-bed`,
  `--no-eq-carve`, `--no-duck`.
- `--gpu-workers` above 4 is known to crash consumer NVIDIA cards; default
  is safe.
- Everything works CPU-only; GPU is an optimization, not a requirement.

## 4. Machine-output contract

- **Exit codes**: `0` success · `1` runtime failure (bad inputs, missing
  tool, generation error — stderr/stdout has the reason, possibly as a
  traceback) · `2` usage error (bad flags; argparse message on stderr).
- **`--json` commands** print exactly one JSON object on stdout:
  `commands`, `where`, `doctor`, `tools`, `library-list`, `video-check`,
  `video-validate`, `video-audio-audit`, `audio-takes-list`,
  `style-detect`, `narration-check`, `series-plan`. Check the
  `ok` field where present.
- **Marker lines** inside human output (grep for them, ignore the rest):
  - `MANGAEASY_PROGRESS <n>/<total> [label]` — progress ticks.
  - `MANGAEASY_RESULT {"outputs": ["<abs path>", ...]}` — final line of a
    successful generation command (`video`, `video-render`, `video-join`,
    `video-add-bgm`, `video-normalize-audio`, `download`, `webtoon-split`,
    `page-split`, `thumbnail-compose`, `setup`); tells you exactly what was
    produced (the split commands also list per-item `verify_images` to
    inspect).
- Output is UTF-8 on every platform, including piped stdout on Windows.
- Long-running commands stream plain log lines; `\r`-style progress
  redraws may appear when a TTY is attached — safe to ignore in pipes.

## 5. Command reference (groups)

Run `mangaeasy commands --json` for the always-current list and
`mangaeasy <command> --help` for flags. Highlights per group:

**Setup** — `where`, `commands`, `doctor`, `setup` (one-command
provisioning), `bootstrap-tools`, `install-tool <name>`, `tools`,
`library-list`, `series-plan` / `series-mark-published` (fixed 12-per-video
upload batches: what's next, what's published — see the recipe below), `mcp`.

**External tools** — `index-tts`, `deepseek-ocr2`, `zimage` (Z-Image Turbo
text-to-image: `mangaeasy zimage --prompt "..." --output out.png --width
1280 --height 720 [--count 4] [--seed N]`; prints `MANGAEASY_RESULT` with
the generated files; needs `install-tool z-image-turbo` once).

**Video pipeline (the recommended workflow)** —
`video` (all-in-one: audio → render → optional join/normalize/BGM),
`video-audio` (Kokoro TTS), `video-audio-indextts` (IndexTTS, GPU),
`video-render`, `video-join`, `video-normalize-audio`, `video-add-bgm`,
`video-check`, `video-validate`, `video-audio-audit [--fix]`,
`video-fade-audio`, `video-clean-audio|video|work|all`,
`audio-takes-list`, `audio-takes-restore`.

**Manga acquire & crop** — `download`
(MangaDex; `--url <title url>` needs no config file, `--all` grabs the whole
series start to end — politely, resumably — `--chapter N` / `--chapters
0-12 14 20.5` for specific ones, always preferring the fullest version when
several scanlations share a number), `style-detect` (webtoon vs paged
verdict + sample pages to eyeball), `webtoon-split` (vertical strips),
`page-split` (paged manga, MAGI v3), `gutter-split` (low-level engine),
`narration-check` (structural narration validation before audio). The
crop → verify → narrate loop is documented in
[operate/crop-verify-narrate.md](operate/crop-verify-narrate.md).

**Image export & AI context** — `to-pdf`, `to-pdf-lossless`,
`convert-images`, `watermark`, `thumbnail-compose` (text furniture onto a
thumbnail base — see [thumbnail.md](thumbnail.md)), `ai-zip`.

## 6. Recipes

Set once for readability (absolute paths recommended):

```bash
ROOT=/abs/path/to/workspace          # any folder the user chose
PROJ=$ROOT/library/myproject         # items live here: $PROJ/01, $PROJ/02, ...
AUDIO=$ROOT/audio  OUT=$ROOT/output  WORK=$ROOT/work
```

### MangaDex URL → published recap series (the full loop)

The end-to-end production flow — download → batch plan → crop+verify →
narrate+verify → video → thumbnail → upload → next batch — is written as an
agent skill: [`.claude/skills/manga-recap/SKILL.md`](../.claude/skills/manga-recap/SKILL.md)
(auto-discovered by Claude Code in this repo; readable as a plain runbook by
any agent). The short of it:

```bash
mangaeasy download --url "<mangadex url>" --all       # whole series, politely
mangaeasy series-plan --project-root library/<P> --json   # → next batch (12 items)
mangaeasy style-detect --project-root library/<P> --json  # webtoon or paged?
mangaeasy webtoon-split --project-root library/<P> --item-range 01-12   # or page-split
# inspect verify_images, clear every suspect, re-split with --overrides if needed
# write narration.json per item, then:
mangaeasy narration-check --project-root library/<P> --item-range 01-12 --json
mangaeasy video --project-root library/<P> --audio-root audio --output-root output \
    --item-range 01-12 --tts auto --build-long-video --normalize-audio \
    --background-music <music>
mangaeasy zimage --prompt-file thumb_prompt.txt --output thumb.png --count 4
mangaeasy thumbnail-compose --base thumb_02.png --output final_thumb.png \
    --text "3-5 PUNCHY WORDS"
mangaeasy youtube-upload --video output/<P>/<P>_full.mp4 --title "..." \
    --thumbnail final_thumb.png --json
mangaeasy series-mark-published --project-root library/<P> --items 01-12 \
    --video-id <id>
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
    --work-dir "$WORK" --item-range 01-12 --tts indextts \
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

## 7. Uploading to YouTube

Preconditions (once, by the **human** — a browser consent is required, an
agent cannot do it): the user creates their own Google OAuth client and
connects it — pasted values (`mangaeasy youtube-auth --client-id <id>
--client-secret <secret>`), a file (`--client-secrets <file>`), or Setup
tab → YouTube account; full walkthrough in [youtube.md](youtube.md).

```bash
mangaeasy youtube-status --json     # {"connected": true, "channel_title": ...}
mangaeasy youtube-status --verify --json  # + live check: {"verified": true, ...}
mangaeasy youtube-upload --video /abs/path/video.mp4 \
    --title "My Recap" --tags "manga,recap" --privacy private --json
# → MANGAEASY_RESULT {"video_id": "...", "url": "https://youtu.be/...", "privacy": "private"}
```

Agent rules for uploads:

- If `youtube-status --json` says `"connected": false`, do **not** attempt
  auth yourself — tell the user to connect (Setup tab or `youtube-auth`).
- `--privacy`: follow the channel owner's instruction. This repo's owner
  wants uploads **published directly — pass `--privacy public`** (see
  docs/recap-video-playbook.md, Phase 11). The CLI *default* stays
  `private` because YouTube force-locks uploads from personal (unaudited)
  API projects to "Private (locked)" regardless of the requested value —
  if an upload arrives private despite `public`, stop and tell the user
  (the fix is YouTube's API audit, not re-uploading).
- Quota: one upload = 1,600 of 10,000 daily units (~6 uploads/day,
  resets midnight Pacific). A `quotaExceeded` error means wait, not retry.
- Uploads are resumable and LONG-RUNNING; progress comes as
  `MANGAEASY_PROGRESS <bytes>/<total>` lines.
- `youtube-logout` disconnects; never read or print the token files under
  `<data>/.mangaeasy/youtube/`.

## 8. Environment variables

| Variable | Meaning |
|---|---|
| `MANGAEASY_ROOT` | Override the data root (app root) — where models, tools, and projects live. Set it to run against a specific install's data. |
| `MANGAEASY_HOME` | Override just the `.mangaeasy` data dir (default `<root>/.mangaeasy`). |
| `MANGAEASY_TOOLS_DIR` | Override where AI tool envs live. |
| `PROJECT_ROOT`, `AUDIO_ROOT`, `OUTPUT_ROOT`, `WORK_DIR` | Defaults for the corresponding `--*-root` flags. Agents should pass explicit flags instead. |
| `KOKORO_ROOT`, `INDEX_TTS_ROOT`, `MAGI_V3_ROOT`, `DEEPSEEK_OCR2_ROOT`, `Z_IMAGE_TURBO_ROOT` | Point at externally-managed tool envs (rarely needed). |
| `MANGAEASY_SHARE_CACHES` | `1` to let external-tool subprocesses inherit an ambient `HF_HOME`/`UV_CACHE_DIR`/… instead of the isolated ones (a shared cross-project cache). Off by default — see below. |

HF/torch/uv caches for external-tool subprocesses are **force-pinned** under
the data folder (`<data>/.mangaeasy/{hf_cache,torch_cache,uv_cache}`), so a
global `HF_HOME`/`UV_CACHE_DIR` you exported for other tools can't scatter
multi-GB model downloads outside the install folder — deleting the folder
really does leave nothing behind. Set `MANGAEASY_SHARE_CACHES=1` to opt into
a shared ambient cache instead (models already downloaded there are then
reused rather than re-fetched under `.mangaeasy`).

## 9. MCP server

```bash
mangaeasy mcp        # newline-delimited JSON-RPC 2.0 over stdio
```

Register: `claude mcp add mangaeasy -- mangaeasy mcp` (or client config
`{"command": "mangaeasy", "args": ["mcp"]}`; add `"env": {"MANGAEASY_ROOT":
"..."}` to run against a specific install's data). Tools: `doctor`, `where`,
`library_list`, `video_check`, `video_validate`, `audio_audit`,
`generate_audio`, `render_videos`, `build_long_video`, `add_bgm`,
`run_full_pipeline`, `bootstrap_tools`, `install_tool`, `generate_image`,
`youtube_status`, `youtube_upload`. Tool results are a
JSON text block: `exit_code`, parsed `report` (for `--json` commands),
parsed `result` (the `MANGAEASY_RESULT` payload), and tail `output`.
Generation/install tools block until done — that can be many minutes.

## 10. Troubleshooting

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
