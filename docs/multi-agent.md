# Multi-agent production — coordination, resume, QA loops, reuse

mangaEasy assumes any number of agents (or one agent across many interrupted
sessions) may work the same project. Coordination state lives in
`library/<Project>/.workboard/` — it travels with the project, works over a
network share, and item scanning ignores it. The filesystem stays the single
source of truth for *work done*; the workboard only coordinates *work in
progress*.

## The session protocol

Every session — first or fiftieth, alone or with other agents running —
starts the same way:

```bash
mangaeasy work-status --project-root library/<Project> --json   # where is everything?
mangaeasy work-status --project-root library/<Project> --next   # what should I grab?
```

`work-status` derives each item's stage (`download → crop → transcribe →
narrate → audio → render`) purely from files on disk, so it is correct even
if the previous agent died mid-run. It also shows live claims and the latest
shared notes. `--next` lists only the unclaimed, actionable tasks.

Then loop:

1. **Claim** the task before touching it:
   `mangaeasy work-claim --project-root library/<P> --item 07 --stage narrate --agent me`
   Exit 0 = yours (lease default 60 min); exit 1 = someone live holds it —
   pick another task. Long job still running? `--renew`. Done? `--release`.
   Expired leases are taken over automatically, so a crashed agent never
   wedges the board.
2. **Hold the GPU mutex around GPU model work.** `page-split`,
   `panel-transcript`, and TTS (`video` / `video-audio-indextts`) each load
   a multi-GB model — two at once on a consumer card is an OOM:
   `mangaeasy work-claim --project-root library/<P> --resource gpu --agent me`
   (release it the moment the GPU step exits; NVENC rendering does not need
   it). Give it a `--ttl-minutes` that covers the whole job.
3. **Write down what the next agent needs.** Character names, speaker
   conventions, tone decisions, warnings — the facts that otherwise die with
   your context window:
   `mangaeasy work-note --project-root library/<P> --add "Labyris = red-haired knight, tsundere, calls Chrome 'onii-chan'" --topic characters`
   Read the notebook before narrating anything: `mangaeasy work-note --project-root library/<P> --list`.
   Narration written by different agents must agree on names and voice — the
   notebook is how.
4. **Verify with the QA loop** (below), release your claims, repeat from
   `work-status --next`.

Claims are advisory by default. When you want them *enforced*, the heavy
commands (`video`, `page-split`, `webtoon-split`, `panel-transcript`) accept
`--respect-claims [--agent me]`: they abort with exit 1 — naming the holder —
if another live agent's claim covers any selected item at that stage.

Project-level stages (`join`, `thumbnail`, `upload`) are single-agent by
nature — claim them without `--item`:
`mangaeasy work-claim --project-root library/<P> --stage join --agent me`.

## The fix-until-clean QA loop (built for small models)

`mangaeasy work-qa` aggregates every machine-checkable gate over the
generated artifacts — crops exist, OCR coverage, narration structure
(dangling images, empty text, intro/narration overlap), speakability,
emotion-field lint, audio coverage + integrity, render freshness — into one
ordered problem list. **Every problem carries the exact fix command.** A
small model needs no global judgment; the whole correction workflow is:

```bash
until mangaeasy work-qa --project-root library/<P> --items 07 --errors-only --json; do
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
  vision-pass job: `mangaeasy narration-review-sheets` then Read every sheet.
- `severity: "info"` = normal-but-worth-confirming (e.g. uncovered
  credits/banner panels).

## Reuse before regenerate

Everything expensive is archived, never clobbered (`old/run_NNNN/`,
timestamped long videos, hash-cached music beds). Before regenerating
anything, check the inventory:

```bash
mangaeasy work-artifacts --project-root library/<P> --json
```

Each category comes with its reuse hint — the important ones:

- **item renders** are reused as-is by `video-join`; `mangaeasy video
  --skip-audio` re-renders only stale items.
- **TTS audio** is reused by any rerun without `--overwrite-audio`;
  overwritten takes are archived — `mangaeasy audio-takes-list` /
  `mangaeasy audio-takes-restore` bring an old take back without
  regenerating.
- **transcripts** (`<item>/transcript.json`) mean any chapter can be
  re-narrated without re-running OCR.
- **music beds** are cached by content hash; `mangaeasy video-add-bgm`
  reuses them automatically.

## Emotion-aware narration (IndexTTS2)

Narration entries may carry an optional `"emotion"` field — a 1–3 word
natural-language phrase (`"tense"`, `"cold, menacing"`, `"tearful"`) that
IndexTTS2 blends into the cloned voice for that one line
(`emo_text`, strength `--emo-alpha`, default 0.6):

```json
{"image": "07_013_01.png", "narration": "Time Stop.", "emotion": "cold, menacing"}
```

- The vocabulary and usage rules live in
  [../mangaeasy/assets/prompts/narration.md](../mangaeasy/assets/prompts/narration.md):
  use it sparingly (reveals, battle cries, goodbyes); most lines stay
  neutral.
- Engine-portable: Kokoro simply ignores the field. Older IndexTTS2 builds
  without `emo_text` fall back to neutral delivery with a warning instead of
  failing the run.
- Tune or disable per run: `mangaeasy video --emo-alpha 0.4` /
  `--no-emotion`. `work-qa` lints malformed emotion fields.

## MCP

All five commands are exposed as MCP tools (`work_status`, `work_claim`,
`work_note`, `work_qa`, `work_artifacts`) by `mangaeasy mcp`, so agent hosts
get the same coordination surface as the CLI.
