# MediaConductor

> One agent-native toolkit for manga videos, narrated AI stories, and song/lyrics videos.

[![CI](https://github.com/tawhidUnhappy/MediaConductor/actions/workflows/ci.yml/badge.svg)](https://github.com/tawhidUnhappy/MediaConductor/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.10%E2%80%933.12-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/core-MIT-green)](LICENSE)

MediaConductor (formerly **mangaEasy**) is a production-oriented CLI and MCP
server that lets an LLM plan, generate, verify, render, and explicitly publish
long-form media. It has no GUI. Heavy AI projects run in separate `uv`
environments so incompatible Torch/CUDA stacks never enter the small core
environment.

```text
Manga Video                 AI Story                    Song & Lyrics Video
download/import             written source              lyrics + prompt/audio
→ crop + visual QA          → continuity bible          → ACE-Step generation
→ narration + TTS           → batched scene art          → Demucs vocals
→ render + QA               → contact-sheet QA          → WhisperX timing
→ explicit upload           → narration + render        → canonical lyric video
                             → explicit upload           → explicit upload
```

## Start from a repository link

An AI agent can set up the complete application from only this URL:

```bash
git clone --depth 1 https://github.com/tawhidUnhappy/MediaConductor.git
cd MediaConductor
uv sync
uv run mediaconductor modes --json
uv run mediaconductor setup --mode ai-story   # or manga-video / song-video
uv run mediaconductor doctor --mode ai-story --json
```

Then point the agent to [AGENTS.md](AGENTS.md). It routes the request to one
small mode skill and prevents the other pipelines from flooding model context.
`mediaconductor modes --json` also returns the absolute `skill_path` for each
mode, including wheel and frozen installations where the skills are bundled
inside the package.

Requirements for a source clone are Git and
[`uv`](https://docs.astral.sh/uv/); uv provisions a compatible Python
3.10–3.12 interpreter when needed. `setup` vendors ffmpeg/ffprobe and other
core executables into the application data folder. NVIDIA is optional; CPU
fallbacks work but large image/music models can be very slow.

Install the command globally instead:

```bash
uv tool install git+https://github.com/tawhidUnhappy/MediaConductor.git
mediaconductor modes
```

`mangaeasy` remains an equivalent compatibility command for existing scripts.
The internal Python import remains `mangaeasy` during the 2.x migration.

## Three isolated modes

### Manga Video

```bash
mediaconductor setup --mode manga-video
mediaconductor commands --mode manga-video --json --full
mediaconductor mcp --mode manga-video --allow-root D:/MediaProjects
```

This is the mature manga/manhwa/webtoon pipeline: MangaDex acquisition,
webtoon or paged-manga crops, visual verification sheets, OCR-grounded
narration, Kokoro/IndexTTS, item and long-video rendering, music mixing,
thumbnails, QA, and YouTube.

See [the Manga Video skill](skills/manga-video/SKILL.md).

### AI Story

```bash
mediaconductor setup --mode ai-story
mediaconductor story-init --project-root projects/my-story \
  --title "The Last Lantern" --story-file story.txt
# An agent completes projects/my-story/story.json.
mediaconductor story-check --manifest projects/my-story/story.json --json
mediaconductor story-build --manifest projects/my-story/story.json --stage all
```

Schema-v2 `story.json` stores the source story, a fixed style contract,
immutable character/environment cards, an ordered per-scene state ledger,
narration, deterministic seeds, provenance, rights, and YouTube metadata. The
default contract was distilled from the supplied fantasy-webcomic references;
see [the complete art specification](docs/ai-story-art-style.md).

The first image pass creates character and environment reference sheets. Each
approval binds both the manifest contract and the generated file hashes; the
same contract/artifact pair protects the final scenes, and the reviewed video
is bound to its SHA-256. Editing an input, regenerating, or replacing a file
invalidates the downstream approval and archives stale output. These controls
make drift visible and reproducible, but every generative backend still needs
visual review before claiming identity continuity. Publishing is never
included in `--stage all`; after artifact-bound video review, rights and
consent confirmation, use the explicit `--stage publish`.

See [the AI Story skill](skills/ai-story/SKILL.md).

### Song & Lyrics Video

```bash
mediaconductor setup --mode song-video
mediaconductor song-init --project-root projects/my-song \
  --title "Open Sky" --lyrics-file lyrics.txt \
  --music-prompt "cinematic synth pop, clear lead vocal, hopeful chorus"
mediaconductor song-check --manifest projects/my-song/song.json --json
mediaconductor song-build --manifest projects/my-song/song.json --stage all
```

The mode can also ingest a finished song with `song-init --audio song.wav`.
ACE-Step 1.5 generates new audio; the maintained Demucs fork isolates vocals;
WhisperX supplies word timing. The supplied lyrics remain authoritative—ASR
spelling never silently replaces them, and structural tags such as `[Chorus]`
are not displayed. JSON records confidence and unmatched words; SRT/ASS is
rebuilt from the approved JSON before render. Generation, separation, timing,
visual, font/style, and final video are content-hash bound. Exit code `3`
means “review and resume,” not generation failure.

The default editable visual prompt begins with **“minimalistic sky”**. Lyrics
use the bundled Edo SZ face in a centered 7clouds-like treatment: white text,
a restrained outline, a small shadow, and per-line fade-in/fade-out. All style
values remain editable in `render.lyrics_style`, and canonical lyrics remain
the text authority. The final video uses the full song mix, not the separation
stems. Public upload requires explicit rights, voice-consent, and
synthetic-media disclosure fields.

See [the Song Video skill](skills/song-video/SKILL.md).

## MCP server

Start a small, mode-scoped stdio server:

```bash
mediaconductor mcp --mode ai-story --allow-root D:/MediaProjects
```

Generic client configuration:

```json
{
  "mcpServers": {
    "media-conductor-story": {
      "command": "mediaconductor",
      "args": ["mcp", "--mode", "ai-story", "--allow-root", "D:/MediaProjects"]
    }
  }
}
```

The no-mode server exposes only the router/readiness/job catalog. Use
`--all-tools` only for legacy/debug clients; it costs far more context. Hidden
mode tools are rejected even if a client calls them directly. Background jobs
accept a typed MCP tool and validated argument object—never raw command lines.
Each repeatable `--allow-root` confines direct arguments, nested job arguments,
and external paths embedded in Story/Song manifests. If it is omitted, the
server allows only its startup working directory. This same-user stdio boundary
reduces accidental filesystem reach; it is not an operating-system sandbox.

Long-running calls must use `job_start`, then `job_status`:

```json
{
  "tool": "job_start",
  "arguments": {
    "tool": "story_build",
    "arguments": {"manifest": "D:/MediaProjects/story/story.json", "stage": "images"}
  }
}
```

Shell-only agents can use the equivalent detached runner:

```powershell
mediaconductor job-start --tool story_build --arguments-json '{"manifest":"D:/MediaProjects/story/story.json","stage":"images"}'
mediaconductor job-status <job-id> --json
```

`job-status` accepts only the generated id returned by `job-start`. Use
`--jobs-dir` to select a different state root; JSON file paths and traversal
segments are rejected.

For a containerized stdio server, keep application state and media in the
mounted `/data` workspace:

```bash
docker build -t media-conductor .
docker run --rm -i -v D:/MediaProjects:/data media-conductor \
  mcp --mode song-video --allow-root /data
```

The image exposes no unauthenticated network port. Run setup against the same
persistent volume first (`... media-conductor setup --mode song-video`) so its
isolated tools and model snapshots survive container replacement.

## Isolated external tools

Each tool lives under the managed tools directory with its own interpreter,
dependency graph, caches, adapter, model provenance, and `READY.json`.
`doctor` treats that marker as a local completeness record: an installer-managed
model must still have its model directory and either every declared file or at
least one real payload file outside the Hugging Face metadata cache.

Every model snapshot downloaded by `mediaconductor install-tool` is locked to
an immutable Hugging Face commit and checked against a required-file allowlist.
Source checkouts are also commit-pinned. Kokoro, MAGI, and the optional generic
Faster Whisper integration still obtain model weights on first use, so those
three paths are explicitly documented as non-reproducible follow-ups.

| Tool | Role | Source strategy |
|---|---|---|
| Kokoro 82M | CPU-friendly narration | pinned optional source clone; model weights resolve on first use |
| IndexTTS 2 | voice-cloned narration | pinned source commit and HF model revision |
| Z-Image Turbo | thumbnails/story/song art | pinned HF model revision and required-file allowlist |
| ACE-Step 1.5 | song generation | pinned source commit, uv lock, and HF model revision |
| Demucs 4.1 | vocal separation | pinned maintained fork + revisioned local `HTDemucs-ft` snapshot; runtime is offline |
| WhisperX 3.8.6 | vocal timing | pinned source plus local HF faster-whisper and English Wav2Vec2 alignment snapshots |
| MAGI v3 | manga panel detection | pinned optional source clone; remote-code model resolves on first use |
| DeepSeek-OCR 2 | panel OCR | pinned source commit and HF model revision |

Install or inspect one tool:

```bash
mediaconductor install-tool whisperx
mediaconductor tools --json
mediaconductor doctor --mode song-video --json
```

All HF, Torch, uv Python, Triton, TorchInductor, NLTK, and extension caches are
redirected below the application data directory. Set `MANGAEASY_SHARE_CACHES=1`
only when deliberately opting into global caches. Existing `MANGAEASY_*`
environment names and `.mangaeasy` data directories remain stable in 2.x so
multi-gigabyte installs are never silently moved.

## Safety and publishing

- Story/song `all` builds stop locally; upload is a separate stage.
- Public song/story upload requires rights/provenance, voice consent, review,
  and synthetic-media disclosure acknowledgements.
- `publish.json` prevents accidental repeat uploads.
- Destructive cleanup is not exposed in Story/Song MCP catalogs. Full cleanup
  requires an allowed generated root and exact directory-name confirmation.
- MCP path arguments, typed background jobs, and manifest-linked source files
  are confined to the server's repeatable `--allow-root` workspace boundary.
- Project item and claim identifiers reject path traversal.
- OAuth JSON is atomically written with owner-only file permissions where the
  platform supports them. Tokens are never printed.
- Multiple named YouTube profiles can isolate manga, song, and AI-story
  channels, or one profile can be reused across modes. Discover them with
  `mediaconductor youtube-profiles --json`; it reports the predefined shared
  Desktop-app client path. One client JSON supports every profile, while each
  keeps an isolated token/channel. A live status/upload opens browser consent
  automatically when needed; `--no-auto-auth` disables that for headless use.
- MediaConductor ships no music or voice samples. Supply only media you are
  licensed and authorized to use.

YouTube OAuth requires a one-time browser action by the channel owner. Follow
[docs/youtube.md](docs/youtube.md). API projects that have not passed Google's
audit can have uploads forced to private regardless of the requested setting.

## Machine contract

- `0`: success.
- `1`: runtime/validation failure.
- `2`: invalid CLI usage.
- `3`: artifact created, but human/agent QA approval is required.
- `--json` commands print one JSON report.
- Generation commands finish with `MANGAEASY_RESULT {...}` for 2.x
  compatibility.
- Progress lines use `MANGAEASY_PROGRESS current/total label`.

Use `mediaconductor commands --mode <mode> --json --full` instead of scraping
help text.

## Development and production checks

```bash
uv sync
uv run ruff check .
uv run python -m compileall -q mangaeasy
uv run pytest
```

CI covers Windows, Linux, and macOS. Frozen releases use a console executable
so MCP stdio remains available. See [SECURITY.md](SECURITY.md),
[docs/production-audit.md](docs/production-audit.md), and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## License

The MediaConductor core is MIT licensed. External tools and models keep their
own licenses; verify each license and the rights to all input/output media for
your use case.
