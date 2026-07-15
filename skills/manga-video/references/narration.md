# Grounded manga narration

Read this reference after crop approval and `panel-transcript`, before TTS.

## File contract

Create `<project-root>/<chapter>/narration.json` as a UTF-8 JSON array in
playback order. Each object requires an image basename that exists in the same
chapter's `panels/` folder and non-empty text to speak. A short `emotion` phrase
is optional for IndexTTS; Kokoro ignores it.

```json
[
  {
    "image": "ch01_001.png",
    "narration": "At the ruined gate, Mina realizes the guards have already fled.",
    "emotion": "quiet concern"
  },
  {
    "image": "ch01_002.png",
    "narration": "Ren points toward the smoke and warns her that someone is still inside."
  }
]
```

The effective schema is:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "array",
  "items": {
    "type": "object",
    "required": ["image", "narration"],
    "properties": {
      "image": {"type": "string", "minLength": 1},
      "narration": {"type": "string", "minLength": 1},
      "emotion": {"type": "string", "minLength": 1, "maxLength": 60}
    },
    "additionalProperties": true
  }
}
```

`intro.json` is optional and uses the same shape. Its entries play before
`narration.json`; do not reference the same panel in both files. Do not replace
machine-generated `transcript.json` with narration.

## Authoring and review

For each panel, read the panel image and its matching `transcript.json` entry.
Describe only visible action and story context supported by the source. Keep
names, pronouns, relationships, abilities, locations, and speaker attribution
consistent across adjacent panels and chapters. Treat OCR as evidence, not as
infallible text: compare it with the bubble before quoting or paraphrasing.

Write natural spoken prose. Avoid inventing dialogue, motives, off-panel
events, or visual details. Avoid narrating credits, scanlator notices, and
purely decorative/SFX panels unless they carry story information. Keep array
order equal to the intended reading/playback order.

Run both gates:

```bash
<mc> narration-check --project-root D:/MediaProjects/library/example --items 01 --json
<mc> narration-review-sheets --project-root D:/MediaProjects/library/example --items 01 --work-dir D:/MediaProjects/work --output-root D:/MediaProjects/review/narration
```

`narration-check` verifies structure and file references. It cannot establish
semantic accuracy. Open every review sheet and compare its panel, narration,
and OCR columns. Correct mismatched panels, speaker errors, unsupported claims,
and awkward spoken phrasing, then rerun both gates. If TTS audio already exists,
use `narration-edit --prune-audio` or the documented audio-audit repair flow so
changed lines are regenerated.
