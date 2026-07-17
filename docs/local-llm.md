# Local LLM (Gemma 4) — assist commands for small driver agents

MediaConductor's manga workflow was designed for a strong multimodal agent:
it must pick the right splitter, *look at* crop verify sheets, write grounded
narration with correct speaker names, and review its own output. In real
production runs, smaller or text-only driver agents (or agents driven from
chat UIs without vision) skipped exactly those steps — wrong splitter on a
webtoon, unreviewed forced cuts shipped into narration, invented character
names.

The assist layer moves that vision-and-judgement work into the toolkit
itself, running Google's **Gemma 4 E4B** (Apache-2.0, text + image input)
locally in an isolated tool env. The driver agent — of any size — only has to
run commands and read exit codes.

```bash
mediaconductor install-tool gemma-4     # ~6 GB; CPU-capable, GPU via Vulkan
```

## The one-command path

```bash
mediaconductor manga-auto --url "<MangaDex title URL>" --name example
# ... runs: download → style-detect → the CORRECT splitter → crop-qa →
#           panel-transcript → characters --auto-draft → narrate-auto
# exits 3 with a review checklist (sheets + reports + characters.json)

# after reviewing/fixing:
mediaconductor manga-auto --project-root library/example --stage build
# ... runs: video (TTS + render + join + normalize) → video-validate → work-qa
```

Every stage is the ordinary CLI command in a subprocess — identical logs and
artifacts, resumable by re-running. `manga-auto` never publishes; thumbnails,
music, and YouTube remain explicit separate steps (see the manga-video skill).

**Exit code 3 always means "artifacts ready — review them".** It is not an
error, and it is not permission to skip the review.

## The building blocks

### `crop-qa` — automated crop review

```bash
mediaconductor crop-qa --project-root library/example --items 01 --work-dir work
```

For webtoon items it renders a full-resolution window around every forced
auto-split cut and short panel from the `webtoon-split` ranges manifest (same
geometry as `webtoon-cutcheck`) and asks Gemma per window: *does this cut
slice a figure or speech bubble?* For paged items it reviews every `page-split`
overlay for missed/merged/misordered boxes.

Every FIX verdict is printed with the exact fix command
(`webtoon-override --merge-at-cut ...` / a `page-split --overrides` entry) and
recorded in `work/crop_qa/<project>/<item>_report.json`. Exit 3 = apply the
fixes, re-split, re-run until exit 0. Unreadable model replies are counted as
`unreviewed` and also force exit 3 — nothing silently passes QA.

The model is a reviewer, not an oracle: spot-check FIX verdicts on the
referenced window images before large re-crops.

### `characters` — the cast registry

```bash
mediaconductor characters --project-root library/example --auto-draft
mediaconductor characters --project-root library/example          # validate/show
```

`<project-root>/characters.json` holds canonical names, aliases, appearance
cues, and roles. `--auto-draft` samples panels across the series and drafts it
with Gemma (names only when attested by OCR/on-panel text, descriptive handles
otherwise). Drafts are always written with `"draft": true` — review the names
against the story, then set `draft: false`. `narrate-auto` injects the
registry into every prompt so speaker attribution stays consistent across
chapters; hand-written narration should use it the same way.

### `narrate-auto` — grounded narration drafts

```bash
mediaconductor narrate-auto --project-root library/example --items 01
```

Chunk by chunk (default 8 panels per vision request) it feeds Gemma the panel
images, their `transcript.json` OCR, the character registry, and a running
story-so-far summary; banner/credit panels are skipped, and panels the model
can't handle are left for manual narration (warned, never invented). It then
runs `narration-check` and renders `narration-review-sheets`, and exits 3:
**read every review sheet** and fix wrong speakers/claims with
`narration-edit` before TTS — the same gate human-written narration goes
through. Existing `narration.json` files are never overwritten without
`--overwrite`.

### `llm` — raw access

```bash
mediaconductor llm --prompt "Who is in this panel?" --image panels/ch01_004.jpg
mediaconductor llm --batch-manifest requests.json     # one model load, many requests
```

Text + images in, text (optionally JSON-schema-constrained) out. Useful for
custom checks; the assist commands are built on the same call path
(`tools/gemma.py:batch_generate`).

## Guardrails that back all of this up

These run regardless of whether Gemma is installed:

- **Style guard** — `webtoon-split` refuses pages that measure as paged manga
  and `page-split` refuses vertical strips, each naming the correct command
  (override with `--force-style` for genuinely mixed items). Running the wrong
  splitter was the most expensive small-agent failure we saw.
- **Workspace resolution** — `setup` registers the workspace; commands started
  from a wrong cwd resolve the registered workspace instead of silently
  creating a second `library/` tree. `download` prints its destination before
  any network work, and `where --json` reports `workspace_root`.

## Driving MediaConductor with a local LLM as the agent

The assist commands intentionally reduce what the *driver* needs: it only has
to run CLI commands (or MCP tools) and react to exit codes 0/1/2/3. Workable
drivers, in order of least friction:

1. **Cline or Roo Code (VS Code extensions)** — both are agentic (terminal
   execution + MCP client) and both support local models via Ollama/OpenAI-
   compatible endpoints. Point them at a local Gemma 4 (e.g. `ollama run
   gemma4`) and register the MCP server:
   `mediaconductor mcp --mode manga-video --allow-root <workspace>`.
2. **Continue (VS Code/JetBrains)** — good local-model support; agent mode is
   lighter than Cline's, fine for the `manga-auto` happy path.
3. **Any OpenAI-compatible chat UI with MCP support** (LM Studio ≥ 0.3.17,
   Open WebUI with an MCP bridge) — expose the mode-scoped MCP server and let
   the model call typed tools; the `job_start`/`job_status` pattern keeps
   long stages out of request timeouts.
4. **llama-server directly** — MediaConductor's own gemma-4 install doubles as
   a general local endpoint: run `gemma-4/llama/.../llama-server -m
   gemma-4/model/gemma-4-E4B-it-Q4_0.gguf --jinja --port 8080` and point any
   OpenAI-compatible client at it.

Whatever the driver, keep it on the rails: `commands --mode manga-video --json
--full` for discovery, background jobs for anything long-running, and treat
exit 3 as "look at the listed artifacts before continuing".
