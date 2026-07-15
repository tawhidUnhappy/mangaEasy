# AI Story art and continuity specification

This specification turns the supplied visual examples into a repeatable, non-artist-specific production contract. It tells an authoring LLM what to put in `story.json`, what MediaConductor injects into generated prompts, and what a reviewer must reject.

## What the reference set establishes

The examples use a clean fantasy-webcomic language. Characters have anime-influenced proportions, elegant silhouettes, large expressive eyes, simplified noses and mouths, carefully shaped hair, and readable hand poses. Dark, slightly organic ink lines carry most of the form. Color is flat and restrained; shadows are sparse and soft rather than painterly. Backgrounds range from simple charcoal-to-slate gradients to warm open landscapes. Props and architecture are selective, not densely rendered.

The visual storytelling alternates among face close-ups, chest-up dialogue shots, over-the-shoulder framing, wide group staging, profile views, and occasional dynamic action. Composition stays immediately readable. Empty space is intentional. Dark scenes favor navy, charcoal, burgundy, and muted brown with red or cool-cyan accents. Open scenes favor cream, pale gold, desaturated grass, and soft sky colors. A small comedic beat may use chibi proportions, but normal anatomy returns immediately afterward.

MediaConductor encodes that observation as `clean-fantasy-webcomic-v1`. Do not mention filenames, a creator, or a living artist in generation prompts. Do not copy characters, dialogue, costumes, or plot elements from the examples. They establish rendering and visual grammar only.

## Required authoring output

An LLM must output one valid schema-version-2 `story.json`. It must preserve the source story's facts while adding production detail in four layers:

1. A global style contract. Keep every `production.style_contract` field present and concrete.
2. Immutable character and environment cards. One stable lowercase ID represents one visual design for the entire project.
3. An ordered scene-state ledger. Every visible character, object, injury, costume, position, time, weather condition, and environment change is carried forward explicitly.
4. A shot instruction and narration for each scene. The shot instruction controls only this frame; the narration says what the audience hears.

### Immutable character card

Write observable facts, not alternatives or mood words. A strong card looks like this:

```json
{
  "id": "mira",
  "name": "Mira",
  "apparent_age": "24",
  "appearance": "warm brown skin; narrow oval face; dark amber almond eyes; slim build; waist-length black hair with a blunt fringe",
  "wardrobe": "default: charcoal high-collar tunic, burgundy piping, black fitted trousers, matte black ankle boots",
  "wardrobe_variants": {
    "travel-cloak": "default outfit plus one calf-length charcoal cloak with a burgundy clasp"
  },
  "signature_features": [
    "one small crescent scar under the left eye",
    "two smooth swept-back black horns of equal length"
  ],
  "never_change": [
    "scar remains under the left eye",
    "horn count, curve, and length remain identical",
    "eye color remains dark amber"
  ]
}
```

Do not write `young woman with pretty eyes, sometimes braided hair`. It permits redesign. Record an apparent age, exact face and eye geometry, skin, hair silhouette/length, body build, immutable nonhuman anatomy, distinguishing marks, and exact default clothes. A wardrobe change is a named variant, never prose invented inside a scene.

### Immutable environment card

Describe a navigable place whose geometry can be checked:

```json
{
  "id": "council-room",
  "name": "Council room",
  "visual_anchor": "circular stone room with a round walnut table centered under a shallow domed ceiling",
  "fixed_elements": [
    "eight straight-backed chairs evenly spaced around the table",
    "single iron door on the north wall",
    "three narrow windows on the west wall"
  ],
  "palette": "charcoal stone, muted walnut brown, burgundy textile accents",
  "lighting": "cool window light from camera-right with one low warm candle accent"
}
```

Fixed geometry must not move between shots. Temporary changes belong in the scene state, not the card.

### Scene-state ledger

Every scene needs a complete state, even when nothing changed:

```json
{
  "id": "council-warning",
  "characters": ["mira"],
  "location": "council-room",
  "render_mode": "standard",
  "transition": {"kind": "continuous"},
  "continuity_state": {
    "previous_scene_id": "council-arrival",
    "time_of_day": "late afternoon, continuous from council-arrival",
    "weather": "steady rain outside",
    "environment_state": [
      "north door closed",
      "one candle burning at the table center",
      "Mira's wet travel cloak hangs on chair three"
    ],
    "changes_from_previous": [
      "Mira removed travel-cloak and placed it on chair three",
      "Mira moved from the north door to the east side of the table"
    ],
    "character_state": {
      "mira": {
        "wardrobe_id": "default",
        "position": "standing at the east side of the table, facing west",
        "condition": "uninjured; fringe and shoulders still damp",
        "emotion": "controlled alarm",
        "held_items": ["sealed ivory letter"]
      }
    }
  },
  "image_prompt": "Medium three-quarter shot from the southwest; Mira plants the sealed letter on the table with her right hand, eyes fixed offscreen left; the candle separates her silhouette from the dark wall.",
  "narration": "Mira placed the sealed warning where every councillor could see it."
}
```

For scene one, set `previous_scene_id` to `null` and use `"changes_from_previous": ["opening state"]`. Every later scene must name the immediately preceding scene ID and the exact delta. Do not use `same as before`; repeat the current truth. `held_items` may be an empty array. A visible character needs a state entry. An offscreen character must not have one.

## Prompt and narration rules

Set `render_mode` to `standard` for normal scenes. Use `chibi` only for an intentionally authored comedic beat, then return to `standard` in the next scene. The authoring LLM writes only the content after `SHOT INSTRUCTION`. Include:

- one story beat;
- shot size and camera angle;
- action with left/right hand when relevant;
- gaze and emotion;
- screen direction and spatial relationship;
- foreground, middle ground, and background when needed;
- the focal light or contrast.

Do not restate hair, face, wardrobe, location geometry, palette, style, or negative prompts. The builder injects the canonical locks. Do not request captions or speech bubbles; narration supplies the words.

Narration must be speakable, faithful, and synchronized to the image. Keep names, tense, viewpoint, causality, and chronology consistent. Describe facts the frame supports. Do not narrate camera directions. When dialogue matters, include a speaker ID and keep the spoken line distinct from explanatory narration.

## Deterministic locking and approvals

Each style, character card, and environment card receives a deterministic hash lock in the expanded prompt. Scene seeds are derived from the project base seed and stable scene ID. These controls prevent accidental prompt drift; they do not make text-only diffusion mathematically identity-perfect.

`prompts/image_batch.json` also maps each scene to its approved character and environment files in `reference_images`. A multi-reference-capable image generator may condition on those files as well as the expanded prompt. The bundled Z-Image adapter records but does not use those sheets as identity-conditioning inputs.

Every scene also declares a transition. A `hard-cut` is independent text-to-image and is mandatory for scene one, location/time jumps, major camera changes, and substantially different casts. A `continuous` scene must immediately follow a frame in the same location. MediaConductor then passes that prior output to Diffusers' supported `ZImageImg2ImgPipeline`, using `production.continuous_transition_strength` (`0.45` by default) or a per-scene value between `0.35` and `0.65`. If an upstream frame regenerates, every downstream continuous frame regenerates in order.

Img2img helps carry low-level layout, palette, costume, pose, and environment structure. It is not a character-identity guarantee. Too little strength can freeze the previous action; too much can drift. It can also propagate an error, so use a hard cut for a large visual delta and keep both approval gates.

Implementation references: Hugging Face's official [Z-Image img2img API](https://huggingface.co/docs/diffusers/api/pipelines/z_image#image-to-image) defines the image/strength behavior, and the official [pipeline loading guide](https://huggingface.co/docs/diffusers/main/en/using-diffusers/loading#reusing-models-in-multiple-pipelines) documents component sharing with `from_pipe` without a second weights allocation.

Production therefore has two mandatory visual gates plus a video gate:

1. Generate and approve `review/reference_contact_sheet.jpg`. Copy both digests from `review/reference_generation.json` into the matching manifest approval fields.
2. Generate and approve `review/story_contact_sheet.jpg`. Copy both digests from `review/scene_generation.json` into the matching manifest approval fields.
3. Review the complete audiovisual result and copy `review/video_generation.json.sha256` into `review.approved_video_sha256`.

Changing a style field, rule, card, state, scene prompt, seed, generated image, or rendered video invalidates its contract/artifact approval. Scene approval is also bound to the exact approved reference-sheet artifacts, so replacing references requires new scene generation and review rather than merely copying the old scene digest. This prevents a stale checkbox from authorizing newly changed output.

Generation provenance is stored in `review/reference_generation.json`, `review/scene_generation.json`, and `review/video_generation.json`. Contract digests detect authored-input changes; content hashes detect regeneration or file replacement. The video contract additionally records FPS, requested and resolved TTS, narration, speaker/emotion fields, and the content hash of an IndexTTS speaker WAV. Keep that voice reference available and unchanged until publication. The builder archives stale images, forces stale narration/video inputs to rebuild, and blocks each downstream stage until the exact new artifacts are reviewed.

Reject a reference or scene if any of these changes without an explicit ledger entry:

- face geometry, apparent age, skin, eye color, hairline, hair length, horns, ears, tail, scar, or body build;
- garment cut, color, closure, trim, jewelry, shoes, or named wardrobe variant;
- handedness, held object, injury, dirt, wetness, or emotional carryover;
- door/window count, furniture position, horizon, fixed prop, palette, light direction, weather, or time;
- character screen direction or relative position in a continuous exchange;
- webcomic line/color language, except an explicitly authored comedic chibi beat.

If a frame fails, correct its card/state/shot wording if ambiguous, regenerate only that frame, rebuild the contact sheet, and approve the new digest. Never approve by hiding the inconsistency in review notes.
