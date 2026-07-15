# Production audit — 2026-07-15

This is the implementation audit that expanded mangaEasy into MediaConductor.
It records every issue identified during the repository, UX, security,
toolchain, packaging, and workflow review; it is not a claim that no future
defect can exist. “Fixed” means covered in the 2.0 change set and automated
tests. “Follow-up” is documented honestly rather than hidden behind a
production-ready claim.

## Release blockers

| Problem | Improvement | Status |
|---|---|---|
| One 43-tool MCP catalog consumed about 8k tokens before workflow context. | Router catalog plus `--mode manga-video|ai-story|song-video`; filter both list and calls. | Fixed |
| Raw `job_start(command, argv)` bypassed MCP curation and could invoke destructive/unlisted commands. | Accept only a mode-visible typed MCP tool and validate its normal argument object. | Fixed |
| `video-clean-all --dir` could recursively delete an unrelated directory. | Require strict `--allowed-root`, non-symlink target, and exact `--confirm-name`; keep cleanup out of Story/Song MCP. | Fixed |
| Narration item and work-claim names allowed path traversal. | Validate direct-child/slug components and resolved parent containment. | Fixed |
| Direct CLI names/subfolders or configured media subdirectories could escape their documented roots (`--project-name`, MangaDex names/chapters, panel subdirs/prefixes, archived runs). | Central portable child-path validation rejects absolute, drive/UNC, traversal, unknown prefix fields, reserved, and non-portable forms while allowing Unicode and internal spaces; argparse reports clean usage errors. | Fixed |
| Tool import verification only warned; any partial directory appeared installed. | Make verification fatal, write `READY.json` after success, and report `ready` plus health problems. | Fixed |
| Frozen PyInstaller build used GUI mode and could lose MCP stdio. | Build a console executable and keep stdio available. | Fixed; frozen handshake added to release workflow follow-up below |
| Container startup exposed no intentional media workspace boundary. | Provide a production Docker entry point whose default MCP root is the persistent `/data` volume; document mode-scoped stdio invocation and expose no network port. | Fixed |
| No AI Story or Song implementation existed. | Add versioned manifests, high-level CLI/MCP tools, isolated setup profiles, render/publish stages, skills, and tests. | Fixed |
| `story-build all` implicitly queued a YouTube upload before a timestamped render path existed. | Execute stages sequentially; `all` stops locally; resolve the real output before explicit publish; persist `publish.json`. | Fixed |
| Song ASR could become incorrect displayed lyrics. | Treat supplied lyrics as canonical, use ASR only for timing, emit raw evidence/confidence/unmatched words, and stop at review code 3. | Fixed |

## Product and LLM design

| Problem | Improvement | Status |
|---|---|---|
| Manga-specific name no longer fit three products. | MediaConductor product/distribution/CLI/MCP identity; retain `mangaeasy` command and Python import for compatibility. | Fixed |
| Flat help mixed internal, destructive, and mode-irrelevant commands. | Three-mode front door and mode-filtered machine catalog; low-level flat commands remain for compatibility. | Fixed |
| Onboarding told every agent to load a large manga guide. | Short `AGENTS.md` router plus independent `manga-video`, `ai-story`, and `song-video` skills. | Fixed |
| One proposed monolithic skill would still overwhelm an LLM. | Tiny router skill; each selected skill has progressive references. | Fixed |
| Shared CLI/MCP/mode/setup registries can drift. | Add registry integrity and scoped-catalog tests. A future unified `CommandSpec` dataclass remains desirable. | Partially fixed |
| 37 historic MCP schemas differed from argparse flags; 23 commands had none. | Add schemas for all new mode commands and critical setup/publish flags, plus strict MCP unknown/type/enum validation. | Partial; complete parser generation is follow-up |
| Default help still exposed too much detail. | Show modes and primary commands; use `commands --mode ... --json --full` for agents. | Fixed |

## AI Story

| Problem | Improvement | Status |
|---|---|---|
| No durable continuity model. | Schema-v2 `story.json` style contract, immutable character/environment cards, ordered scene-state ledger, narration, prompts, QA and rights state. | Fixed |
| Random scene generation made resume irreproducible. | Store a base seed and derive deterministic per-scene seeds unless explicitly supplied. | Fixed |
| Z-Image reloaded for every scene. | Batch manifest and one adapter process/model load. | Fixed |
| Text-only image generation was described as consistent without qualification. | Generate character/environment sheets first, bind both image passes to contract digests, archive stale art, and block each next stage until approval. | Fixed |
| Every scene was independent even when the story called for continuous action. | Classify each transition as `hard-cut` or `continuous`; use text-to-image for hard cuts and Z-Image image-to-image from the approved previous frame for compatible continuous scenes, with bounded strength and downstream invalidation. | Fixed |
| No dedicated identity adapter. | Preserve reference-image metadata, immutable character cards, deterministic seeds, digest locks, contact-sheet QA, and previous-frame image conditioning. A project LoRA/face adapter remains a future capability; do not claim guaranteed identity. | Partial |
| No explicit visual grammar for the supplied examples. | Inspect all 20 references and document a non-artist-specific fantasy-webcomic style contract, exclusions, prompt order, character cards, environment cards, and review checklist. | Fixed |
| A missing scene image could fail deep inside FFmpeg with an opaque traceback. | Add a clean video preflight that verifies current reference/image approvals and digests plus every required scene image. | Fixed |
| No visual/video approval or rights gate before upload. | Require current image approvals/digests for render and video/rights/voice/disclosure confirmation for publish. | Fixed |
| Contract-only approval could survive `--overwrite` or an on-disk file replacement. | Persist invalidation before replacement; bind reference sheets, scene frames, and the exact rendered video to generated-file hashes as well as contract digests. | Fixed |

## Song & Lyrics Video

| Problem | Improvement | Status |
|---|---|---|
| No song generation, separation, alignment, or lyrics renderer. | Add ACE-Step, maintained Demucs, WhisperX, canonical alignment, ASS/SRT/JSON, FFmpeg renderer, and high-level build. | Fixed |
| Requested Facebook Demucs repository is archived. | Use author-maintained `adefossez/demucs` commit and explicit HF safetensors model. | Fixed |
| Heavy dependencies would conflict in one environment. | Four distinct uv environments: ACE-Step, Demucs, WhisperX, Z-Image. | Fixed |
| Generic Torch override would break ACE-Step's platform lock. | Mark ACE-Step as upstream-locked and never force-reinstall its Torch stack. | Fixed |
| Torch index mapping omitted Torchaudio. | Route Torch, Torchvision, and Torchaudio together. | Fixed |
| Models and source floated. | Pin audited source commits and immutable HF revisions for ACE-Step, Demucs, WhisperX/Faster-Whisper, the English aligner, IndexTTS, DeepSeek-OCR 2, and Z-Image. Kokoro, MAGI, and generic Faster Whisper first-use model resolution remains explicitly disclosed. | Fixed for installer-managed snapshots; partial for first-use integrations |
| Demucs could contact Hugging Face at runtime or silently select a different model. | Prefetch the exact YAML and four safetensors files, enforce offline mode, and route the maintained Demucs loader through a local allow-list adapter. | Fixed |
| WhisperX alignment could trigger a hidden runtime download. | Prefetch the pinned English aligner and NLTK data, pass the local model path, and run Transformers/Hugging Face offline. Other alignment languages are rejected until pinned packs are defined. | Fixed, English currently supported |
| WhisperX's Silero VAD still used `torch.hub` and could fetch code/model data from GitHub despite HF offline flags. | Pin the Silero wheel and install a fail-closed local Torch Hub shim that serves only its bundled JIT model/utilities. | Fixed |
| No lyric QA for repeated choruses/misspellings. | Sequence alignment, canonical output, monotonic timing tests, confidence and explicit approval. | Fixed; more multilingual fixtures desirable |
| Existing ASS files were treated as proof that current lyrics/audio/style had been aligned, and JSON timing corrections never reached render. | Bind generation, separation, alignment, visual, render inputs, and video to content digests; rebuild SRT/ASS from the approved canonical JSON before every render. | Fixed |
| Common `[Verse]`/`[Chorus]` structure markers appeared as sung subtitles. | Skip a strict allow-list of whole-line structural markers while preserving arbitrary bracketed sung text. | Fixed |
| Demucs `auto` inferred CUDA from host `nvidia-smi`, even inside a CPU-only tool environment. | Resolve `auto` with `torch.cuda.is_available()` inside the isolated Demucs interpreter. | Fixed |
| Publishing lacked rights and AI disclosure. | Rights/voice fields plus YouTube `containsSyntheticMedia` and explicit-only publish. | Fixed |
| Generic subtitle presentation did not match the requested music-channel look. | Bundle the licensed Edo SZ face, center white lyrics over minimalistic-sky art, use a restrained outline/small shadow, and add bounded line fade-in/fade-out. | Fixed |
| Word-by-word karaoke highlighting. | Current output is line-timed ASS. Per-word karaoke effects are a future enhancement. | Follow-up |

## Installation and supply chain

| Problem | Improvement | Status |
|---|---|---|
| Model downloads used floating HF revisions. | ToolSpec passes immutable `--revision` and required-file allowlists for every installer-managed snapshot, using a pinned Hugging Face CLI. First-use Kokoro, MAGI, and generic Faster Whisper models remain follow-up. | Fixed for installer-managed snapshots; partial overall |
| Detached commit updates attempted `git pull`. | Immutable refs use fetch + detached checkout; only branches pull. | Fixed |
| Full Git clones wasted bandwidth before checking out one pinned commit. | Use shallow, blob-filtered, no-tag fetches with Git LFS smudging disabled; pin each cloned source and installer-managed HF snapshot. | Fixed |
| Caches leaked through uv-managed Python, Triton, NLTK and Torch extensions. | Redirect these caches under the application home. | Fixed |
| Install was not staged/atomic and had no rollback. | `READY.json` plus local snapshot validation prevents missing or empty model payloads from reporting ready; staging + atomic directory promotion remains follow-up. | Partial |
| Binary bootstrap followed moving releases, trusted archives without integrity checks, and retained an unsafe bulk extractor. | Pin uv 0.11.16 and Git LFS 3.7.1 with embedded SHA-256 values; verify BtbN FFmpeg against its current checksum manifest; require HTTPS, bounded/atomic downloads and bounded single-member extraction; remove bulk extraction. The macOS FFmpeg provider publishes no checksum, so a trusted system install remains preferred there. | Fixed on Windows/Linux; partial on macOS |
| No disk/VRAM/native-runtime preflight. | Existing GPU detection remains basic; add disk, cuDNN/CTranslate2, FFmpeg filter/shared-library and VRAM checks. | Follow-up |
| Stable user data for global `uv tool` installs is ambiguous. | Preserve current path compatibility in 2.x; define a deliberate migration rather than silently moving multi-GB models. | Follow-up |

## Jobs, MCP, and server hardening

| Problem | Improvement | Status |
|---|---|---|
| Job IDs used a racy existence loop. | Add random UUID suffix. | Fixed |
| Status polling loaded whole logs into memory. | Stream marker scans and keep a bounded 500-line tail. | Fixed |
| Concurrent Windows status reads could make an atomic state replacement fail, leaving a finished job marked orphaned. | Use unique per-write temporary files and bounded retry around the atomic replace. | Fixed |
| `job-status` accepted arbitrary JSON paths and trusted a state-controlled log path. | Accept generated job IDs only, enforce jobs-root containment, and derive the sibling log path instead of following state data. | Fixed |
| No cancel/timeout/retry/heartbeat/GPU scheduler. | Add durable task cancellation and resource scheduling in a later release. | Follow-up |
| MCP echoed unsupported protocol versions. | Negotiate only explicitly supported protocol versions and fall back to the current supported revision. An official SDK migration remains optional. | Fixed |
| Long direct MCP calls can block the stdio loop. | Descriptions and mode skills require typed jobs; server-enforced async-only for every long tool remains desirable. | Partial |
| MCP clients could reach arbitrary same-user filesystem paths. | Add a repeatable `--allow-root` policy (startup directory by default) covering direct arguments, nested typed jobs, configured defaults, and external Story/Song manifest paths. Document that this is a stdio guardrail, not an OS sandbox. | Fixed |
| Malformed JSON-RPC and unbounded requests/reports could destabilize stdio. | Return parse/invalid-request errors, cap requests at 1 MiB, and bound parsed reports/output. Cancellation and pagination remain follow-up. | Partial |
| MCP logged full argv values and large inline stories/lyrics could exceed Windows process limits. | Log argument names only and bridge bounded inline story/lyrics text through owner-only managed temporary files that are deleted in `finally`. | Fixed |
| Non-object MCP `params`/`arguments` could raise without replying, hanging the client. | Return JSON-RPC `-32602` and sanitize internal error responses. | Fixed |

## Publishing, credentials, and repository hygiene

| Problem | Improvement | Status |
|---|---|---|
| OAuth files were non-atomic with normal permissions. | Atomic replacement and mode 0600 where supported. OS keyring/Windows ACL hardening remains possible. | Fixed/partial |
| One token cache could not represent separate manga, story, and song channels. | Add safe named YouTube profiles with independent token/channel state; each mode manifest selects a profile and profiles may target distinct channels or reuse one. | Fixed |
| Every account appeared to require a duplicate Google Cloud client file and manual reauthorization. | Define one shared Desktop OAuth client JSON, retain optional per-profile override compatibility, and automatically open browser consent when an online operation finds no usable token. A headless opt-out is explicit. | Fixed |
| Upload resume state existed only in-process; thumbnail failure could be partial success. | Project `publish.json` prevents duplicate high-level publishes. Durable session URLs and partial-success state remain follow-up. | Partial |
| A successful high-level upload ignored failure to save `publish.json`, allowing a blind retry. | Treat idempotency-state failure as a fatal, explicit reconciliation condition and never report normal success. | Fixed |
| Project examples lived under unignored `projects/`, so a routine add or Docker build could include stories, lyrics, art, and audio. | Ignore `/projects/` in Git and Docker and test representative media paths plus distribution allow-lists. | Fixed |
| About 150 MB of unlicensed/unclear music and voice WAVs were tracked. | Remove them from current HEAD/releases and require user-owned media/provenance. | Fixed for HEAD |
| Removed WAV blobs remain in Git history and still affect full clones. | A `git filter-repo` history rewrite/force-push needs explicit owner coordination because it invalidates existing clones. | Follow-up requiring approval |
| Third-party notices were stale. | Replace with current tool/model source, revision, and license notes. | Fixed |
| Thumbnail docs used unprofessional/risky terminology. | Replace with neutral high-energy key-art guidance. | Fixed |
| CI covered only Python 3.12 and release docs referenced a missing file. | Add minimum-version coverage, correct links/names, and frozen MCP handshake. | Fixed |
| macOS silently lacked its brand icon; Windows ICO contained only 16×16; Docker included build artifacts. | Use the tracked PNG as PyInstaller's macOS conversion source, verify the bundle resource, regenerate a true multi-resolution ICO, and exclude build/dist/user-media contexts. | Fixed |

## Acceptance gates

The change set adds mode registry tests, hidden-tool/job escape tests, path traversal/deletion tests, deterministic story prompt/materialization tests, canonical lyric alignment/repeated-chorus tests, mode setup dry-runs, and existing end-to-end rendering tests. Full model downloads/inference remain unsuitable for ordinary CI; schedule pinned nightly GPU smoke tests for ACE-Step, Demucs, WhisperX, and Z-Image.
