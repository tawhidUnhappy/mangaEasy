# Multi-agent production — coordination, resume, QA loops, reuse

MediaConductor assumes any number of agents (or one agent across many interrupted
sessions) may work the same project. That "one agent across interrupted
sessions" case explicitly includes **switching which LLM is driving** —
Claude runs out of budget mid-batch, and GPT (or any other model) picks the
exact same project back up. Nothing in the workboard is Claude-specific or
tied to one chat session: it's plain JSON/JSONL under
`library/<Project>/.workboard/`, so it travels with the project, works over a
network share, and item scanning ignores it. The filesystem stays the single
source of truth for *work done*; the workboard coordinates *work in
progress* and *working memory* — the facts and plans that would otherwise
live only in one model's context window. See
[Switching LLM providers mid-project](#switching-llm-providers-mid-project)
below for the handoff recipe.

## The session protocol

Every session — first or fiftieth, alone or with other agents (or other
models) running — starts the same way:

```bash
mediaconductor work-status --project-root library/<Project> --json   # where is everything?
mediaconductor work-status --project-root library/<Project> --next   # what should I grab?
```

`work-status` derives each item's stage (`download → crop → narrate →
audio → render`, with `transcribe` surfacing only when a panel-transcript
run was started and left unfinished — OCR itself is optional) purely from
files on disk, so it is correct even if the previous agent died mid-run. It
also shows live claims, the latest shared notes, and open todos (below) in
one report — that combination is the full resume briefing a fresh agent
needs, regardless of what wrote the code that produced it. `--next` lists
only the unclaimed, actionable tasks.

Then loop:

1. **Claim** the task before touching it:
   `mediaconductor work-claim --project-root library/<P> --item 07 --stage narrate --agent me`
   Exit 0 = yours (lease default 60 min); exit 1 = someone live holds it —
   pick another task. Long job still running? `--renew`. Done? `--release`.
   Expired leases are taken over automatically, so a crashed (or simply
   cut-off) agent never wedges the board.
2. **Hold the GPU mutex around GPU model work.** `page-split`,
   `panel-transcript`, and TTS (`video` / `video-audio-indextts`) each load
   a multi-GB model — two at once on a consumer card is an OOM:
   `mediaconductor work-claim --project-root library/<P> --resource gpu --agent me`
   (release it the moment the GPU step exits; NVENC rendering does not need
   it). Give it a `--ttl-minutes` that covers the whole job.
3. **Write down what the next agent needs.** Character names, speaker
   conventions, tone decisions, warnings — the facts that otherwise die with
   your context window:
   `mediaconductor work-note --project-root library/<P> --add "Labyris = red-haired knight, tsundere, calls Chrome 'onii-chan'" --topic characters`
   Read the notebook before narrating anything: `mediaconductor work-note --project-root library/<P> --list`.
   Narration written by different agents must agree on names and voice — the
   notebook is how.
4. **Track plan-level next steps on the shared todo list** — see
   [Session todo list](#session-todo-list) below.
5. **Verify with the QA loop** (below), release your claims, repeat from
   `work-status --next`.

## Session todo list

`work-status` and `work-claim`/`work-note` cover everything derivable from
the filesystem or worth writing down as a fact. But a production run also
carries **plan-level intent** that lives in neither place: "this batch stops
at chapter 24", "the user asked for the ch10 thumbnail redone", "confirm the
tone on that reveal panel before uploading". `work-todo` is a small, ordered,
shared checklist for exactly that — the same working-memory role a coding
agent's own in-session todo list plays, except it's a file next to the
project instead of state inside one process, so it outlives any single
context window or model:

```bash
mediaconductor work-todo --project-root library/<P> --add "Redo ch10 thumbnail text" --topic publishing
mediaconductor work-todo --project-root library/<P> --list
mediaconductor work-todo --project-root library/<P> --start 3   # mark in_progress
mediaconductor work-todo --project-root library/<P> --done 3    # mark done
mediaconductor work-todo --project-root library/<P> --reopen 3  # undo a premature "done"
mediaconductor work-todo --project-root library/<P> --remove 3  # no longer relevant — delete it
```

Open (non-done) todos also appear directly in `work-status`'s report (capped
at 10, with a count of how many more exist), so reading the resume briefing
once surfaces them without a separate call. Storage is an append-only event
log (`todo.jsonl`, same durability model as `notes.jsonl`) — ids are assigned
once and never reused, even after `--remove`, so an id mentioned earlier in
a conversation or a note always means the same todo.

## Switching LLM providers mid-project

The scenario this is built for: you're mid-batch, the current model runs out
of budget or context, and a different one (different vendor, different
session, no shared memory of the conversation) needs to continue as if it
were the same worker. Nothing here is special-cased per model — it's the
ordinary multi-agent protocol above, applied across a vendor boundary
instead of across two processes:

1. **Identify the model in claims/notes/todos** by setting
   `MEDIACONDUCTOR_AGENT` before it runs (e.g. `export
   MEDIACONDUCTOR_AGENT=claude-fable` vs. `gpt-5.6`). Every claim, note, and
   todo records who touched it, so a later agent can tell which decisions
   came from which model — useful when judging whether to trust a stylistic
   call (e.g. a narration tone choice) without re-deriving it.
2. **Before a session ends — planned or forced —** leave a `handoff`-topic
   note describing exactly what was in flight, not just what stage you were
   on: `mediaconductor work-note --project-root library/<P> --topic handoff
   --add "item 14 video-render was running in the background when I was cut
   off; check job-status before re-launching, don't assume it crashed."`
   Filesystem state plus a claim lease already recover *what stage* an item
   is at; the handoff note recovers the *narrative* — the one thing an
   interrupted session can't reconstruct from disk alone. Add any
   not-yet-actioned next step to `work-todo` too.
3. **A session that starts cold** — any model, any vendor — runs exactly the
   step 0 orientation from the top of this doc:
   `work-status --json` (stage + claims + notes + todos in one report), then
   `work-note --list --topic handoff` for the full text of the last
   handoff note (the summary in `work-status` is capped), then proceeds from
   `work-status --next`. There is no prompt or config to port between
   models — the same three commands work whether the previous agent was
   itself, or something else entirely.
4. **Claims outlive the process that took them** (TTL lease, not a
   heartbeat), so a session that vanished mid-GPU-job doesn't need to be
   "informed" that it's gone — the lease simply expires and the next agent's
   `work-claim` takes over automatically. Set `--ttl-minutes` generously for
   long GPU jobs so a slow model download doesn't get pre-empted by an
   impatient takeover.

Claims are advisory by default. When you want them *enforced*, the heavy
commands (`video`, `page-split`, `webtoon-split`, `panel-transcript`) accept
`--respect-claims [--agent me]`: they abort with exit 1 — naming the holder —
if another live agent's claim covers any selected item at that stage.

Project-level stages (`join`, `thumbnail`, `upload`) are single-agent by
nature — claim them without `--item`:
`mediaconductor work-claim --project-root library/<P> --stage join --agent me`.

## The fix-until-clean QA loop (built for small models)

`mediaconductor work-qa` aggregates every machine-checkable gate over the
generated artifacts — crops exist, OCR coverage, narration structure
(dangling images, empty text, intro/narration overlap), speakability,
emotion-field lint, audio coverage + integrity, render freshness — into one
ordered problem list. **Every problem carries the exact fix command.** A
small model needs no global judgment; the whole correction workflow is:

```bash
until mediaconductor work-qa --project-root library/<P> --items 07 --errors-only --json; do
    # read problems[0].fix, run/apply it, repeat
done
```

- Exit 0 = machine-clean; exit 1 = problems remain. Problems come in
  pipeline order, so fixing the first one is always safe.
- `--max-problems` (default 25) keeps the list inside a small context
  window; fix, re-run, the next slice appears.
- `severity: "review"` items are the checks that need **eyes** (crop verify
  sheets, narration review sheets). They never block the loop — resolve them
  by Reading the sheet files listed in the problem, not by retrying.
  Semantic narration QA (right speaker, one beat per panel) stays a
  vision-pass job: `mediaconductor narration-review-sheets` then Read every sheet.
- `severity: "info"` = normal-but-worth-confirming (e.g. uncovered
  credits/banner panels).

## Reuse before regenerate

Everything expensive is archived, never clobbered (`old/run_NNNN/`,
timestamped long videos, hash-cached music beds). Before regenerating
anything, check the inventory:

```bash
mediaconductor work-artifacts --project-root library/<P> --json
```

Each category comes with its reuse hint — the important ones:

- **item renders** are reused as-is by `video-join`; `mediaconductor video
  --skip-audio` re-renders only stale items.
- **TTS audio** is reused by any rerun without `--overwrite-audio`;
  overwritten takes are archived — `mediaconductor audio-takes-list` /
  `mediaconductor audio-takes-restore` bring an old take back without
  regenerating.
- **transcripts** (`<item>/transcript.json`) mean any chapter can be
  re-narrated without re-running OCR.
- **music beds** are cached by content hash; `mediaconductor video-add-bgm`
  reuses them automatically.

## Emotion-aware narration (IndexTTS2)

Narration entries may carry an optional `"emotion"` field. The calm-narration
policy accepts only `"calm"`, `"neutral"`, `"slightly sad"`, or `"slightly
happy"`; IndexTTS2 blends that restrained hint into the cloned voice
(`emo_text`, strength `--emo-alpha`, default 0.6):

```json
{"image": "07_013_01.png", "narration": "He quietly stops time.", "emotion": "calm"}
```

- The vocabulary and usage rules live in
  [../mediaconductor/assets/prompts/narration.md](../mediaconductor/assets/prompts/narration.md):
  omit it for normal lines and never use high-intensity delivery hints,
  phonetic noises, exclamation marks, or shout-like all-caps.
- Engine-portable: Kokoro simply ignores the field. Older IndexTTS2 builds
  without `emo_text` fall back to neutral delivery with a warning instead of
  failing the run.
- Tune or disable per run: `mediaconductor video --emo-alpha 0.4` /
  `--no-emotion`. `work-qa` blocks non-calm emotion hints and loud-delivery
  text before audio generation.

## MCP

All six commands are exposed as MCP tools (`work_status`, `work_claim`,
`work_note`, `work_todo`, `work_qa`, `work_artifacts`) by `mediaconductor mcp`,
so agent hosts get the same coordination surface as the CLI — including a
host running a different model than the one that last touched the project.
