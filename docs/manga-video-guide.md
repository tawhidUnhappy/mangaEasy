# MediaConductor Manga Video guide

This is the detailed document an AI assistant needs after selecting the
`manga-video` mode. Story and Song agents should not load it. Repository
development conventions live in `CLAUDE.md`; this file is about operating the
manga pipeline from a shell.

MediaConductor turns folders of images plus a narration script into narrated videos:
per-chapter ("item") videos, optionally joined into one long video with
background music, ready for YouTube. Everything is driven by one command:
`mediaconductor <subcommand>`. Source-checkout examples use
`uv --project D:/MediaConductor run mediaconductor ...`, which resolves the
checkout even while the agent works in `D:/MediaProjects`. Replace both roots
with absolute local paths. A global uv-tool install may omit the uv prefix;
`mangaeasy` remains only a 2.x compatibility alias.

**Orient yourself with two calls** (do this first on any machine):

```bash
uv --project D:/MediaConductor run mediaconductor where --json
uv --project D:/MediaConductor run mediaconductor commands --mode manga-video --json --full
```

`--full` includes each command's arguments (flag, type, required) and a
`long_running` marker, so you never need to run per-command `--help` to build
a command line.

Prefer MCP? `uv --project D:/MediaConductor run mediaconductor mcp --mode manga-video --allow-root D:/MediaProjects`
runs a scoped MCP stdio server with typed tools —
see [MCP server](#mcp-server) below. The CLI and MCP expose the same engine.

Making a full recap video end to end (panel detection, narration writing,
thumbnail, YouTube upload)? Follow `docs/recap-video-playbook.md` — a
step-by-step production recipe verified on a real published video. This
file is the command reference underneath it.

---

## 1. Getting a working command

All three modes keep the isolation promise: everything MediaConductor writes
stays under one data folder ("app root"), never scattered over the system.

### Mode 1 — uv tool install (recommended for agent environments)

```bash
uv tool install git+https://github.com/tawhidUnhappy/MediaConductor.git
mediaconductor --version
```

### Mode 2 — source checkout

```bash
git clone --depth 1 https://github.com/tawhidUnhappy/MediaConductor.git && cd MediaConductor
uv sync
uv run mediaconductor --version
```

### Mode 3 — a frozen release build (no Python needed)

Each GitHub release ships a self-contained frozen `mediaconductor` per OS.
Unpack it and run the executable directly; MediaConductor is a CLI and MCP
server, not a GUI:

```bash
./MediaConductor/mediaconductor --version
./MediaConductor/mediaconductor commands --mode manga-video
```

To point a run at a specific data root (models, tools, projects), set
`MEDIACONDUCTOR_ROOT` before the command:

```bash
MEDIACONDUCTOR_ROOT="/data/mediaconductor" ./MediaConductor/mediaconductor library-list \
  --project-root "/data/mediaconductor/library" --json
```

Default data folders when `MEDIACONDUCTOR_ROOT` is unset: Windows = next to the exe;
macOS = `~/Library/Application Support/mangaEasy`; Linux =
`~/.local/share/mangaEasy`.

## 2. First-run setup

One command provisions everything (GPU-aware; see [setup.md](setup.md)):

```bash
uv --project D:/MediaConductor run mediaconductor setup --mode manga-video
                                 # add --dry-run to inspect the plan first
```

Or piece by piece:

```bash
uv --project D:/MediaConductor run mediaconductor doctor --mode manga-video --json
uv --project D:/MediaConductor run mediaconductor bootstrap-tools
                                 #   downloaded into the data folder, not system-wide
uv --project D:/MediaConductor run mediaconductor install-tool index-tts
uv --project D:/MediaConductor run mediaconductor install-tool kokoro-82m
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

A *project* is a folder of numbered *items* (chapters). Pass the folder marked
below directly as `--project-root`:

```
D:/MediaProjects/library/<project-name>/   <- --project-root
  manga.json           machine-managed source record (written by `download`):
                       source site, title URL, manga_id, canonical title,
                       per-chapter download info — read this when you need
                       the manga's link or official title
  01/
    download/            source pages: 001.jpg, 002.webp, ...
    panels/              panel images (.png/.jpg/.webp), one per narration entry
    transcript.json      OPTIONAL OCR cross-evidence (panel-transcript)
    narration.json       [{"image": "chapter1_001.png", "narration": "text..."}, ...]
    intro.json           OPTIONAL, same shape — prepended at load time (cold open)
  02/ ...
```

`uv --project D:/MediaConductor run mediaconductor library-list
--project-root D:/MediaProjects --json` includes each project's `manga.json` as a
`manga` field (`null` when absent), so you don't need to read the file
yourself when scanning. Imported pages use the same concrete layout:
`<project>/<chapter>/download/<page image>`. If pages must stay in another
chapter-local folder, pass `--source-subdir <folder>` to `style-detect`,
`webtoon-split`, `page-split`, and `webtoon-cutcheck`; the default is
`download`.

Generated output goes to separate roots you pass explicitly (recommended
for agents — never rely on cwd defaults):

- `--audio-root <dir>` → `<dir>/<project>/<item>/<panel>.wav` raw per-panel
  narration. Production rendering writes symmetric 8 ms edge-faded copies
  under the sibling `<dir>_faded/<project>/...` tree and leaves raw TTS
  untouched.
- `--output-root <dir>` → `<dir>/<project>/items/item_<NN>.mp4` per-item videos
  and `<dir>/<project>/<project>_full_<timestamp>.mp4` joined long videos
- `--work-dir <dir>` → scratch, safe to delete (`video-clean-work`)

Item selection everywhere: `--items 01 02 05-08` or `--item-range 01-12`.

**Safety rules an agent must follow:**

- Never create/delete/rename files inside `library/` items except
  `narration.json`/`intro.json` content edits the user asked for (prefer
  `narration-edit` over hand-editing). The project-level `manga.json` and
  `publish.json` are machine-managed — read them freely, don't hand-edit them.
- Generated output is archived (`old/run_NNNN/`), never overwritten
  silently; use `video-clean-*` commands to clear it, never raw deletes.
- Volume flags are dB-native (negative = quieter), e.g.
  `--music-volume-db -28`. The value is how far the music sits *below the
  measured narration loudness* after the configured narration gain. The music
  stem is aligned to that reference before the offset (disable with
  `--no-music-loudnorm`), so the number is a true LU separation regardless
  of the track's mastering. The full pipeline mixes BGM first, then performs
  one final two-pass whole-mix normalization to −14 LUFS / −1.5 dBTP; that
  last gain affects voice and music together and preserves their separation.
  Any BGM change invalidates final normalization. For narration-driven
  recap videos (dense, wall-to-wall narration) the tuned value is
  **−28 (the default)** — chosen so the bed stays comfortable over a full
  long-form watch instead of fatiguing the listener; a punchier or sparser
  edit can sit at −26 to −22; below −32 the bed risks becoming inaudible on
  phone speakers, above −15 it masks the voice. `video-add-bgm` also, by
  default, compresses the bed's own dynamic
  range so it sits at a consistent level (not swelling on its own), carves
  the 2–5 kHz vocal band, and sidechain-ducks the music under the voice —
  the pro voiceover chain. Opt out per-stage with `--no-condition-bed`,
  `--no-eq-carve`, `--no-duck`.
- `--gpu-workers` above 4 crashes consumer NVIDIA cards, so the CLI clamps
  it to 4 with a warning (`MEDIACONDUCTOR_UNSAFE_GPU_WORKERS=1` overrides on
  tested hardware); default is safe.
- Everything works CPU-only; GPU is an optimization, not a requirement.
- Production manga renders default to `--audio-source faded` with a symmetric
  8 ms fade-in and fade-out per panel. Use `--audio-source raw` only as an
  intentional diagnostic comparison; never destructively process raw TTS.
- `video-validate` is structural validation, not final media approval.
  Separately inspect start/middle/end frames, check narration-to-panel timing,
  audit faded WAV boundaries for clicks, and measure the final complete mix.

## 4. Background jobs (how to run anything long)

Real steps run minutes to hours (TTS, panel detection, OCR, renders,
uploads) — `commands --json --full` marks them `long_running`. Never block a
foreground call on them. If your harness offers background shells with
completion notifications, use those; otherwise (or from MCP) use the built-in
job runner — it works everywhere, including frozen installs:

```bash
uv --project D:/MediaConductor run mediaconductor job-start --tool run_full_pipeline --arguments-json '{"project_root":"D:/MediaProjects/library/example","audio_root":"D:/MediaProjects/audio","output_root":"D:/MediaProjects/output","items":["01-12"],"tts":"auto","build_long_video":true,"normalize_audio":true,"no_background_music":true}'
# -> {"ok": true, "job_id": "20260714-153000-video-a1b2c3d4", "poll": "mediaconductor job-status ..."}
uv --project D:/MediaConductor run mediaconductor job-status 20260714-153000-video-a1b2c3d4 --json
# -> status running/succeeded/failed/orphaned, exit_code, last MEDIACONDUCTOR_PROGRESS,
#    parsed MEDIACONDUCTOR_RESULT, log tail
uv --project D:/MediaConductor run mediaconductor jobs --json
```

The typed `--tool/--arguments-json` form is schema-validated and is what
`commands --json --full` publishes. The positional compatibility form
`uv --project D:/MediaConductor run mediaconductor job-start video [video flags...]`
remains available to existing scripts.

Pass only the generated id to `job-status`. To use a non-default state folder,
select it with `--jobs-dir`; direct JSON paths and traversal segments are
rejected.

The job survives your session: a detached supervisor writes the exit code and
final result into `<work>/jobs/<id>.json` (override dir with
`MEDIACONDUCTOR_JOBS_DIR` or `--jobs-dir`). If the machine slept or the supervisor
was killed, `job-status` reports `orphaned` instead of a forever-"running" lie
— re-run the command (pipeline steps resume/skip completed work). GPU tools
block-buffer stdout, so an empty log tail on a running job is normal; trust
`status` and filesystem signals (crops/transcripts appearing), not log volume.

## 5. Machine-output contract

- **Exit codes**: `0` success · `1` runtime failure (bad inputs, missing
  tool, generation error — stderr/stdout has the reason, possibly as a
  traceback) · `2` usage error (bad flags; argparse message on stderr).
- **`--json` commands** print exactly one JSON object on stdout:
  `commands`, `where`, `doctor`, `tools`, `library-list`, `video-check`,
  `video-validate`, `video-audio-audit`, `audio-takes-list`,
  `style-detect`, `narration-check`, `series-plan`, `job-status`, `jobs`
  (`job-start` always prints one JSON object). Check the `ok` field where
  present.
- **Marker lines** inside human output (grep for them, ignore the rest):
  - `MEDIACONDUCTOR_PROGRESS <n>/<total> [label]` — progress ticks.
  - `MEDIACONDUCTOR_RESULT {"outputs": ["<abs path>", ...]}` — final line of a
    successful generation command (`video`, `video-render`, `video-join`,
    `video-add-bgm`, `video-normalize-audio`, `download`, `webtoon-split`,
    `page-split`, `thumbnail-compose`, `setup`); tells you exactly what was
    produced (the split commands also list per-item `verify_images` to
    inspect).
- Output is UTF-8 on every platform, including piped stdout on Windows.
- Long-running commands stream plain log lines; `\r`-style progress
  redraws may appear when a TTY is attached — safe to ignore in pipes.

## 6. Command reference (groups)

Run `uv --project D:/MediaConductor run mediaconductor commands --mode
manga-video --json --full` for the always-current list and
`uv --project D:/MediaConductor run mediaconductor <command> --help` for flags.
Highlights per group:

**Setup** — `where`, `commands`, `doctor`, `setup` (one-command
provisioning), `bootstrap-tools`, `install-tool <name>`, `tools`,
`library-list`, `series-plan` / `series-mark-published` (fixed 12-per-video
upload batches: what's next, what's published — see the recipe below), `mcp`.

**External tools** — `index-tts`, `deepseek-ocr2`, `zimage` (Z-Image Turbo
text-to-image: `uv --project D:/MediaConductor run mediaconductor zimage
--prompt "..." --output out.png --width
1280 --height 720 [--count 4] [--seed N]`; prints `MEDIACONDUCTOR_RESULT` with
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
`panel-transcript` (optional OCR cross-evidence — the narrating agent may
read bubbles directly from panels instead), `narration-check` (structural
validation),
and `narration-review-sheets` (panel/text/OCR semantic review). The
crop → verify → narrate loop is documented in
[operate/crop-verify-narrate.md](operate/crop-verify-narrate.md).

**Image export & AI context** — `to-pdf`, `to-pdf-lossless`,
`convert-images`, `watermark`, `thumbnail-compose` (text furniture onto a
thumbnail base — see [thumbnail.md](thumbnail.md)), `ai-zip`.

## 7. Recipes

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
uv --project D:/MediaConductor run mediaconductor download --url "<mangadex url>" --all
uv --project D:/MediaConductor run mediaconductor series-plan --project-root "$PROJ" --json
uv --project D:/MediaConductor run mediaconductor style-detect --project-root "$PROJ" --source-subdir download --json
uv --project D:/MediaConductor run mediaconductor webtoon-split --project-root "$PROJ" --item-range 01-12 --source-subdir download --work-dir "$WORK"
# inspect verify_images, clear every suspect, re-split with --overrides if needed
# OCR panels, author grounded narration.json, then inspect every review sheet:
uv --project D:/MediaConductor run mediaconductor panel-transcript --project-root "$PROJ" --item-range 01-12
uv --project D:/MediaConductor run mediaconductor narration-check --project-root "$PROJ" --item-range 01-12 --json
uv --project D:/MediaConductor run mediaconductor narration-review-sheets --project-root "$PROJ" --item-range 01-12 --work-dir "$WORK"
uv --project D:/MediaConductor run mediaconductor video --project-root "$PROJ" --audio-root "$AUDIO" --output-root "$OUT" --work-dir "$WORK" \
    --item-range 01-12 --tts auto --build-long-video --normalize-audio \
    --background-music <music>
uv --project D:/MediaConductor run mediaconductor zimage --prompt-file thumb_prompt.txt --output thumb.png --count 4
uv --project D:/MediaConductor run mediaconductor thumbnail-compose --base thumb_02.png --output final_thumb.png \
    --text "3-5 PUNCHY WORDS"
uv --project D:/MediaConductor run mediaconductor youtube-upload --profile <profile> --video "$OUT/<P>/<P>_full.mp4" --title "..." \
    --thumbnail final_thumb.png --json
uv --project D:/MediaConductor run mediaconductor series-mark-published --project-root "$PROJ" --items 01-12 \
    --video-id <id>
```

### Images folder → narrated video (one item)

```bash
mkdir -p "$PROJ/01/panels"                # put images into panels/
# write $PROJ/01/narration.json: [{"image": "<file in panels/>", "narration": "..."}]
uv --project D:/MediaConductor run mediaconductor video-check  --project-root "$PROJ" --audio-root "$AUDIO" --items 01 --json
uv --project D:/MediaConductor run mediaconductor video-audio  --project-root "$PROJ" --audio-root "$AUDIO" --items 01
uv --project D:/MediaConductor run mediaconductor video-render --project-root "$PROJ" --audio-root "$AUDIO" \
    --output-root "$OUT" --work-dir "$WORK" --items 01
# → MEDIACONDUCTOR_RESULT {"outputs": [".../items/item_01.mp4"], ...}
```

### Batch chapters → one long video with background music

```bash
uv --project D:/MediaConductor run mediaconductor video --project-root "$PROJ" --audio-root "$AUDIO" --output-root "$OUT" \
    --work-dir "$WORK" --item-range 01-12 --tts indextts \
    --build-long-video --normalize-audio \
    --background-music /abs/path/music.mp3 --music-volume-db -28
```

### Re-mix only the background music (cheap — no re-render/re-join)

```bash
uv --project D:/MediaConductor run mediaconductor video-add-bgm --project-root "$PROJ" --output-root "$OUT" \
    --input /abs/path/joined.mp4 --output /abs/path/remixed.mp4 \
    --background-music /abs/path/other.mp3 --music-volume-db -28
uv --project D:/MediaConductor run mediaconductor video-normalize-audio \
    --input /abs/path/remixed.mp4 --replace --target-i -14 --target-tp -1.5
# Pass both paths explicitly: standalone *_bgm_* outputs are not implicit join
# candidates, and every music change requires a new final normalization pass.
```

### Resume an interrupted audio run

```bash
uv --project D:/MediaConductor run mediaconductor video-audio --project-root "$PROJ" --audio-root "$AUDIO" \
    --item-range 01-12 --resume
```

### Audit & repair audio before a long build

```bash
uv --project D:/MediaConductor run mediaconductor video-audio-audit --project-root "$PROJ" --audio-root "$AUDIO" --json
uv --project D:/MediaConductor run mediaconductor video-audio-audit --project-root "$PROJ" --audio-root "$AUDIO" --fix
uv --project D:/MediaConductor run mediaconductor video-audio --project-root "$PROJ" --audio-root "$AUDIO"
```

### Restore a previous audio take instead of regenerating

```bash
uv --project D:/MediaConductor run mediaconductor audio-takes-list --project-root "$PROJ" --audio-root "$AUDIO" --json
uv --project D:/MediaConductor run mediaconductor audio-takes-restore --project-root "$PROJ" --audio-root "$AUDIO" --run run_0003
```

## 8. Uploading to YouTube

First run the offline profile discovery command. It returns the predefined
`shared_client_file` path without returning OAuth contents. The user creates
one Google Desktop-app client and places its downloaded JSON there; all named
profiles normally reuse it while keeping isolated tokens/channel caches. Never
paste client or token JSON into an agent prompt. See [youtube.md](youtube.md).

```bash
uv --project D:/MediaConductor run mediaconductor youtube-profiles --json
uv --project D:/MediaConductor run mediaconductor youtube-status --profile <profile> --verify --json
uv --project D:/MediaConductor run mediaconductor youtube-upload --profile <profile> --video /abs/path/video.mp4 \
    --title "My Recap" --tags "manga,recap" --privacy private --json
# → MEDIACONDUCTOR_RESULT {"profile": "...", "video_id": "...", "url": "https://youtu.be/...", "privacy": "private"}
```

Agent rules for uploads:

- Treat the profile as publish identity. Match its cached channel title/id to
  the user's intended destination, ask when ambiguous, and pass that exact
  `--profile` to status, upload, thumbnail, list, and delete. Never silently
  fall back to `default`.
- If live status finds no usable token, it opens Google consent automatically;
  start the call, wait for the channel owner to approve the browser page, and
  let the same command continue. Expired/revoked or API-rejected credentials
  follow the same one-reauthorization retry path. Use `--no-auto-auth` only for
  a headless worker configured in advance.
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
  `MEDIACONDUCTOR_PROGRESS <bytes>/<total>` lines.
- `youtube-logout --profile <profile>` disconnects only that profile; never
  read or print the token files under
  `<data>/.mangaeasy/youtube/`.

## 9. Environment variables

| Variable | Meaning |
|---|---|
| `MEDIACONDUCTOR_ROOT` | Override the data root (app root) — where models, tools, and projects live. Set it to run against a specific install's data. |
| `MEDIACONDUCTOR_HOME` | Override just the `.mangaeasy` data dir (default `<root>/.mangaeasy`). |
| `MEDIACONDUCTOR_TOOLS_DIR` | Override where AI tool envs live. |
| `MEDIACONDUCTOR_ITEMS_ROOT`, `MEDIACONDUCTOR_AUDIO_ROOT`, `MEDIACONDUCTOR_OUTPUT_ROOT`, `MEDIACONDUCTOR_WORK_DIR` | Defaults for the corresponding `--*-root` flags (bare legacy names `PROJECT_ROOT`/`AUDIO_ROOT`/`OUTPUT_ROOT`/`WORK_DIR` still honoured). Agents should pass explicit flags instead. |
| `MEDIACONDUCTOR_JOBS_DIR` | Where `job-start` keeps job state/logs (default `<work>/jobs`). |
| `MEDIACONDUCTOR_UNSAFE_GPU_WORKERS` | `1` disables the `--gpu-workers` clamp at 4 (only on hardware tested higher). |
| `KOKORO_ROOT`, `INDEX_TTS_ROOT`, `MAGI_V3_ROOT`, `DEEPSEEK_OCR2_ROOT`, `Z_IMAGE_TURBO_ROOT` | Point at externally-managed tool envs (rarely needed). |
| `MEDIACONDUCTOR_SHARE_CACHES` | `1` to let external-tool subprocesses inherit an ambient `HF_HOME`/`UV_CACHE_DIR`/… instead of the isolated ones (a shared cross-project cache). Off by default — see below. |

HF/torch/uv caches for external-tool subprocesses are **force-pinned** under
the data folder (`<data>/.mangaeasy/{hf_cache,torch_cache,uv_cache}`), so a
global `HF_HOME`/`UV_CACHE_DIR` you exported for other tools can't scatter
multi-GB model downloads outside the install folder — deleting the folder
really does leave nothing behind. Set `MEDIACONDUCTOR_SHARE_CACHES=1` to opt into
a shared ambient cache instead (models already downloaded there are then
reused rather than re-fetched under `.mangaeasy`).

## 10. MCP server

```bash
uv --project D:/MediaConductor run mediaconductor mcp --mode manga-video --allow-root D:/MediaProjects
```

For a source checkout, register with `claude mcp add media-conductor-manga --
uv --project D:/MediaConductor run mediaconductor mcp --mode manga-video --allow-root D:/MediaProjects` (or
client config `{"command":"uv","args":["--project","D:/MediaConductor",
"run","mediaconductor","mcp","--mode","manga-video","--allow-root","D:/MediaProjects"]}`; add `"env": {"MEDIACONDUCTOR_ROOT":
"..."}` to run against a specific install's data). The tool schemas come from
the same table as `commands --json --full` (`mediaconductor/command_spec.py`), so
CLI and MCP can't drift. Tool results are a JSON text block: `exit_code`,
parsed `report` (for `--json` commands), parsed `result` (the
`MEDIACONDUCTOR_RESULT` payload), and clipped `output`.

**Long-running tools must go through `job_start`** (returns a job id
immediately) + `job_status` / `job_list` polling — a blocking `tools/call`
that runs for minutes to hours will hit the MCP client's timeout. The
descriptions of the long tools say so explicitly.

`--allow-root` is repeatable and confines direct paths, nested typed jobs, and
manifest-linked files. If omitted, the startup directory is the only allowed
root. This is a same-user stdio guardrail, not an OS sandbox.

## 11. Troubleshooting

| Symptom | Likely cause | Do |
|---|---|---|
| `ffmpeg`/`ffprobe` null in doctor | core tools not downloaded | `uv --project D:/MediaConductor run mediaconductor bootstrap-tools` |
| `video-audio` fails to import kokoro | TTS env missing | `uv --project D:/MediaConductor run mediaconductor install-tool kokoro-82m` |
| `video-render` "have no audio yet" | narration changed since audio was generated | `video-audio` again (skips existing), or `video-audio-audit --fix` first |
| Long join fails validation | items missing audio/video | `video-check --json`, fix reported items |
| Output seems stale/missing | it was archived | look in `old/run_NNNN/` next to the output; `audio-takes-list` for audio |
| GPU crash with many workers | too many CUDA contexts | the CLI clamps `--gpu-workers` at 4; unset `MEDIACONDUCTOR_UNSAFE_GPU_WORKERS` |
| Slow first model run | models downloading to the data folder | expected once; offline afterwards |
| `unknown command` | typo | the error suggests near-matches; see `uv --project D:/MediaConductor run mediaconductor commands --mode manga-video --json --full` |

Background music may live in a user-owned `bgm/` folder. MediaConductor ships
no music or voice-cloning samples: set `bgm.file`/`--background-music` and
`tts.speaker_wav`/`--speaker-wav` only to media you are licensed and authorized
to use. Record its provenance with the project.
