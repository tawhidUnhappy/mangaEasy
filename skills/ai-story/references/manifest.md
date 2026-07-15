# Authoring `story.json`

Preserve schema version 2, `mode`, production settings, the complete `style_contract`, and stable lowercase-hyphen IDs.

## Write in this order

1. Read the full source story and extract a chronology, cast, locations, carried props, injuries, wardrobe changes, time, and weather.
2. Create one immutable character card per recurring visual identity. Required fields are `id`, `name`, `apparent_age`, `appearance`, exact default `wardrobe`, `wardrobe_variants`, `signature_features`, and `never_change`.
3. Create one immutable environment card per location. Required fields are `id`, `name`, `visual_anchor`, `fixed_elements`, `palette`, and `lighting`.
4. Split the story into one meaningful visual beat per scene. Preserve chronology and write speakable, faithful narration.
5. Give every scene a complete `continuity_state`, carrying the previous truth forward and naming only the delta in `changes_from_previous`.
6. Give every scene an explicit `transition`. Scene one is always `{"kind": "hard-cut"}`. Use `{"kind": "continuous"}` only for the immediately following beat in the same location.
7. Set `render_mode` to `standard`, except for a deliberate one-scene comedic `chibi` beat.
8. Write `image_prompt` as shot direction only. The builder adds all appearance, wardrobe, environment, state, style, transition, and exclusion text.

## Scene state contract

Every character card must use this shape:

```json
{
  "id": "ari",
  "name": "Ari",
  "apparent_age": "23",
  "appearance": "warm brown skin; narrow oval face; amber almond eyes; slim build; short tightly coiled black hair",
  "wardrobe": "mustard knee-length raincoat, navy straight trousers, brown lace-up boots",
  "wardrobe_variants": {},
  "signature_features": ["amber eyes", "short tightly coiled hair silhouette"],
  "never_change": ["warm brown skin", "oval face", "amber eye color"]
}
```

Every environment card must use this shape:

```json
{
  "id": "old-bridge",
  "name": "Old bridge",
  "visual_anchor": "one mossy stone arch over a narrow north-flowing river",
  "fixed_elements": ["iron lantern post at east entrance", "broken waist-high parapet on south side"],
  "palette": "slate blue, moss green, warm amber accent",
  "lighting": "cool blue-hour ambient light with one warm lantern source"
}
```

Every scene requires this complete shape:

```json
{
  "id": "bridge-crossing",
  "characters": ["ari"],
  "location": "old-bridge",
  "render_mode": "standard",
  "transition": {"kind": "hard-cut"},
  "continuity_state": {
    "previous_scene_id": null,
    "time_of_day": "blue hour",
    "weather": "light rain",
    "environment_state": ["river flowing north", "east lantern post unlit"],
    "changes_from_previous": ["opening state"],
    "character_state": {
      "ari": {
        "wardrobe_id": "default",
        "position": "east entrance, walking west, body facing screen-left",
        "condition": "uninjured; raincoat shoulders lightly wet",
        "emotion": "alert",
        "held_items": ["lit brass hand lantern"]
      }
    }
  },
  "image_prompt": "Wide eye-level shot from the south bank; Ari steps onto the arch toward screen-left and raises the lantern in the right hand; warm light separates the face from the blue rain.",
  "narration": "Ari crossed the old bridge before night erased the path."
}
```

Visible character IDs and `character_state` keys must match exactly. Never write “same as before.” Repeat the current state. Set the first scene's `previous_scene_id` to `null` and its change entry to `opening state`; every later scene must name the immediately preceding scene ID.

For a real continuous beat, set `"transition": {"kind": "continuous"}`. The builder uses the prior scene PNG as Z-Image img2img input with `production.continuous_transition_strength` (default `0.45`). A per-scene `img2img_strength` may override it only from `0.35` through `0.65`. Lower values retain more of the old frame and may resist the requested action; higher values permit more change and may drift. Use `hard-cut` for a new location, time jump, large camera change, or substantially different cast. Img2img is structural continuity assistance, not an identity guarantee.

Run `story-check --manifest <path> --json` after every edit. Fix every reported path. Contract digests come from the report/generation records; generated-file digests come from `review/reference_generation.json` and `review/scene_generation.json`. Copy both only after visually approving the matching contact sheet. A scene-generation record must also name the exact current reference artifact digest; approving new reference sheets never authorizes old scene frames. Any contract edit, regeneration, or file replacement invalidates the old approval. Seeds are deterministic when omitted; store an explicit seed only for an intentional approved variant.

The video-generation record separately binds the current scene artifacts, FPS, requested and resolved TTS engine, every narration/speaker/emotion field, and the SHA-256 of an IndexTTS speaker WAV when one is used. A change to any of them requires narration/video regeneration and a fresh complete-video review. Preserve the recorded speaker WAV until publication so `story-check --for-publish` can verify that voice provenance.

Before publishing, set `youtube.profile` to the exact named account verified
through `youtube-profiles` and `youtube-status --profile <name> --verify`.
Using the same profile for multiple projects/modes is valid; never guess a
different profile from the project title.
