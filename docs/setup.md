# Setup — from a fresh clone to a verified install

The short version, for a machine that already has `git` and `uv`:

```bash
git clone https://github.com/tawhidUnhappy/MediaConductor.git
cd MediaConductor
uv sync
uv run mediaconductor setup --mode manga-video
uv run mediaconductor smoke-test     # proves the install actually produces video
```

(Installed via `uv tool install` or a frozen release instead? Just run
`mediaconductor setup --mode <mode>` then `mediaconductor smoke-test`.)

The rest of this page is the **agent runbook**: the exact sequence an LLM
agent follows on a machine it has never seen, with a machine-checkable
verification step after each stage and a troubleshooting table of real
failures. Every command is non-interactive and safe to re-run.

---

## Agent runbook

### Step 0 — Prerequisites (`git`, `uv`)

Only two host tools are needed; everything else (Python included — `uv`
downloads and pins its own interpreter) is provisioned into the repo folder.

```bash
git --version || echo MISSING git
uv --version  || echo MISSING uv
```

Install `uv` if missing:

| Platform | Command |
|---|---|
| Windows (PowerShell) | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` |
| Windows (winget) | `winget install astral-sh.uv` |
| macOS / Linux | `curl -LsSf https://astral.sh/uv/install.sh | sh` |

After installing, open a fresh shell (or add `~/.local/bin` to PATH) and
re-check `uv --version`. `git` comes from the platform package manager
(`winget install Git.Git`, `apt install git`, Xcode CLT on macOS).

> **Windows note:** never invoke bare `python` — on many machines it is the
> Microsoft Store stub that opens a browser. Always go through
> `uv run mediaconductor ...` / `uv run python ...`.

### Step 1 — Clone and sync the Python environment

```bash
git clone https://github.com/tawhidUnhappy/MediaConductor.git
cd MediaConductor
uv sync
```

`uv sync` creates `.venv/` from the committed lockfile (interpreter
included). Verify:

```bash
uv run mediaconductor --version
uv run mediaconductor where --json     # resolved data/tool paths for THIS install
```

**Run every subsequent command from the repo root.** All data roots
(`library/`, `audio/`, `output/`, `work/`, `.mangaeasy/`) resolve relative
to the install; running from elsewhere is the classic "Failed to spawn:
mediaconductor" / wrong-paths failure.

### Step 2 — Provision binaries, tool envs and models

```bash
uv run mediaconductor setup --mode <manga-video|ai-story|song-video>
```

GPU-aware and profile-driven — what gets installed, in order:

1. **Core binaries** — ffmpeg/ffprobe, uv, git-lfs, vendored into this
   install's own tools dir (~100 MB). uv and Git LFS are version- and
   SHA-256-pinned; Windows/Linux FFmpeg is checked against the publisher's
   checksum manifest. On macOS, prefer a trusted system FFmpeg because that
   bootstrap provider does not publish archive checksums. Nothing goes
   system-wide.
2. **Hardware detection** — NVIDIA GPU check picks the profile.
3. **AI tool environments** — each in its own isolated `uv` env under
   `.mangaeasy/tools/`, models included. With `--mode`, setup installs only
   that pipeline: Manga Video uses Kokoro/IndexTTS/MAGI/DeepSeek/Z-Image; AI
   Story uses Kokoro/IndexTTS/Z-Image; Song Video uses
   ACE-Step/Demucs/WhisperX/Z-Image. Their environments and instructions stay
   separate:

   | Tool env | Installed when | Role | Download budget |
   |---|---|---|---|
   | `kokoro-82m` | always | CPU TTS (universal fallback voice) | ~1 GB |
   | `index-tts` | NVIDIA GPU | voice-cloning TTS (the recap voice) | ~6 GB |
   | `magi-v3` | NVIDIA GPU | panel detection for paged manga | ~4 GB |
   | `deepseek-ocr2` | NVIDIA GPU | panel OCR (`panel-transcript`) | ~7 GB |
   | `z-image-turbo` | NVIDIA GPU | thumbnail/key-art generation | ~33 GB |
   | `ace-step` | Song Video | generated song audio | model-dependent |
   | `demucs` | Song Video | offline vocal separation | model-dependent |
   | `whisperx` | Song Video | offline English lyric timing | model-dependent |

4. **Readiness report** — the same data as `mediaconductor doctor --json`, plus
   a `MANGAEASY_RESULT` line with per-tool ok/failed status.

Useful variants:

```bash
mediaconductor setup --mode ai-story --dry-run # inspect one exact mode plan
mediaconductor setup --minimal                 # core binaries only (fast)
mediaconductor setup --all                     # every cataloged tool, GPU or not
mediaconductor setup --mode song-video --skip z-image-turbo # repeatable skip
mediaconductor setup --mode manga-video --skip-models        # envs now, weights later
mediaconductor setup --mode ai-story --cpu  # or use --cuda to force the torch target
```

Expect the full GPU profile to take tens of minutes on a fast connection —
it is **idempotent and resumable**: if the run is interrupted (network,
power), just rerun the same mode command; it skips what's done and resumes
partial model downloads. One tool failing does not abort the others (exit
code 1 + a named failure in the summary — fix with another `setup` run or
`mediaconductor install-tool <name>`; per-tool options live in
[install-tools.md](install-tools.md)).

### Step 3 — Verify with `doctor --json` (the machine contract)

```bash
uv run mediaconductor doctor --json
```

One JSON object. Assert, for the profile you installed:

- `executables.ffmpeg` and `executables.ffprobe` are non-null paths;
  `executables.uv` non-null.
- `gpu` / `cuda` reflect the hardware you expect (`cuda_device` names the
  card); `gpu_backend` is `cuda`, `mps`, or `cpu`.
- For each tool you installed: `tools.<name>.installed == true`
  (`configured` true means the catalog entry itself is valid).

Anything false → re-run `mediaconductor setup` (or `mediaconductor install-tool
<name>` for one tool) and check again. `doctor` is read-only and cheap; use
it as the fix-loop oracle.

### Step 4 — Prove it end to end with `smoke-test`

```bash
uv run mediaconductor smoke-test
```

Builds a tiny throwaway project (two generated panels + narration),
synthesizes silent audio with ffmpeg, renders a real MP4 through the actual
pipeline (encoder autodetection included — NVENC on NVIDIA, libx264
otherwise), ffprobes the result (h264 + aac, expected duration) and cleans
up after itself. `SMOKE TEST PASS` + exit 0 means this machine can produce
videos. `doctor` says the parts are installed; this proves they work
together.

Optionally prove the TTS toolchain too (downloads the Kokoro model on first
use if `--skip-models` was used):

```bash
uv run mediaconductor smoke-test --tts kokoro
```

`--keep` leaves `work/smoke_test/` behind for inspection on failure.

### Step 5 — Optional per-channel assets (not in the repo)

Nothing below is required — the pipeline runs without them — but recaps
produced for a real channel usually want:

- **Voice-clone reference WAV** (IndexTTS): a clean ~10–30 s speech sample.
  Point `config.system.json → tts.speaker_wav` at it, or pass
  `--speaker-wav` to `mediaconductor video`. Without it, `--tts auto` falls back
  to Kokoro.
- **Background music track**: any music file; pass `--background-music
  <path>`. It is QC'd, conditioned, loudness-aligned and ducked
  automatically (see [recap-video-playbook.md](recap-video-playbook.md)).
- **YouTube upload**: place one downloaded Desktop-app client JSON at the
  `shared_client_file` reported by `mediaconductor youtube-profiles --json`.
  Each named profile keeps its own token/channel; the first live status/upload
  opens browser consent automatically for the channel owner and continues.
  Use `--no-auto-auth` only for a pre-authorized headless worker. See
  [youtube.md](youtube.md).
- **Config files**: none are needed to start. `config.system.json` (copy of
  `config.system.example.json`) holds machine-wide defaults; `config.json`
  holds per-project download defaults — if you copy the example, leave
  `download.name` null: a non-null name there silently overrides the
  project name `download --url` derives from the manga title (the CLI
  prints an `[INFO]` when that happens; agents should pass `--name`
  explicitly instead).

### Fix loop summary

| Symptom | Fix |
|---|---|
| `uv: command not found` | Step 0 install, then open a fresh shell |
| `Failed to spawn: mediaconductor` | you left the repo root — `cd` back before `uv run` |
| `doctor` shows a tool `installed: false` | `mediaconductor install-tool <name>` or re-run `setup` |
| model download interrupted / partial | re-run `mediaconductor setup` (resumes) |
| `ffmpeg not found` in smoke-test | `mediaconductor bootstrap-tools`, re-check `doctor` |
| GPU expected but `cuda: false` | check `nvidia-smi` works on the host; fix drivers, re-run `setup --cuda` |
| no GPU at all | fine — CPU profile: TTS = Kokoro, encoding = libx264; `page-split`/`zimage` need `setup --all` and are slow on CPU |
| disk pressure | `--skip z-image-turbo` saves ~33 GB; `--skip-models` defers the rest |
| corporate proxy blocks Hugging Face | set `HTTPS_PROXY` before `setup`; caches stay in-tree (`.mangaeasy/`) |

### Where everything lands

Self-contained by design: tool envs and model caches under `.mangaeasy/`
(HF/torch/uv caches are force-pinned there — a global `HF_HOME` will NOT
leak downloads elsewhere; set `MANGAEASY_SHARE_CACHES=1` if you want shared
caches; see [external-tools.md](external-tools.md)), projects under
`library/`, generated output under `audio/`, `output/`, `work/`. Deleting
the folder removes everything. `MANGAEASY_ROOT` relocates the data root.

### After setup

- Producing a recap as an agent: follow
  [.claude/skills/manga-recap/SKILL.md](../.claude/skills/manga-recap/SKILL.md)
  (Claude Code loads it automatically) or
  [recap-video-playbook.md](recap-video-playbook.md).
- CLI contract and full command catalog:
  [ai-guide.md](ai-guide.md) / `mediaconductor commands --json`.
