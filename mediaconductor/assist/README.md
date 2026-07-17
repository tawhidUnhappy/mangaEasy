# mediaconductor/assist — local-LLM helpers for small driver agents

The manga pipeline assumes its driver agent can read verify sheets, write
grounded narration, and fix crops. In production, small or text-only agents
skipped exactly those steps (wrong splitter, unreviewed forced cuts, invented
speaker names). This package moves that vision-and-judgement work into the
toolkit itself, running on the isolated **Gemma 4** tool env
(`mediaconductor install-tool gemma-4`, runner in
[`../tools/gemma.py`](../tools/gemma.py)).

See [docs/local-llm.md](../../docs/local-llm.md) for the operator guide.

## Files

| File | Command | Role |
|---|---|---|
| [`characters.py`](characters.py) | `characters` | `<project-root>/characters.json` cast registry (name/aliases/appearance/role) that grounds narration + speaker attribution; `--auto-draft` samples panels and drafts it with Gemma (always `draft: true`) |
| [`crop_qa.py`](crop_qa.py) | `crop-qa` | Gemma-vision review of crop verify artifacts: webtoon forced cuts + short panels (cutcheck-geometry windows) and paged page overlays; prints the exact `webtoon-override` / `--overrides` fix per FIX verdict; exit 3 = fixes proposed |
| [`narrate.py`](narrate.py) | `narrate-auto` | drafts `<item>/narration.json` chunk-by-chunk from panel images + `transcript.json` OCR + the registry, skips banners, chains a story-so-far summary, then runs `narration-check` + review sheets; **always exit 3** — review before TTS |
| [`auto.py`](auto.py) | `manga-auto` | orchestrator: each stage is the normal CLI command in a subprocess (same logs/artifacts, resumable). `--stage prep` ends at a review gate (exit 3); `--stage build` renders + validates after review. Never publishes |

## Gotchas

- **Everything here proposes; humans/agents dispose.** `narrate-auto` and
  `characters --auto-draft` never overwrite existing files without
  `--overwrite`, and both exit 3 so no caller can mistake a draft for a
  reviewed artifact. Don't change those exit codes.
- **`crop-qa` verdict schema is `fix`/`accept`** (webtoon) and `fix`/`ok`
  (paged); anything unparseable becomes `unreviewed` and still forces exit 3 —
  an unreadable model reply must never silently pass QA.
- **Model calls go through `tools/gemma.py:batch_generate()`** (one manifest →
  one server load). `narrate-auto` intentionally calls it once per chunk
  because each chunk's prompt needs the previous chunk's `story_so_far`; don't
  "optimize" that into one batch without redesigning the chaining.
- Prompt-block builders (`registry_prompt_block`, `chunk_prompt`,
  `merge_chunk_entries`) are pure and unit-testable — keep them free of I/O.

## Tests

[tests/test_assist.py](../../tests/test_assist.py) (pure logic — no model).
