# AI Story visual contract

Use the manifest's `production.style_contract` verbatim. It describes the supplied reference set without naming or imitating a particular artist:

- hand-drawn 2D fantasy webcomic rendering with anime-influenced proportions;
- clean, slightly organic dark ink lines and restrained line-weight variation;
- flat cel color, minimal soft shading, sparse highlights, and no painterly or 3D texture;
- elegant readable silhouettes, expressive eyes/hands, and stable face/hair anatomy;
- uncluttered environments with simple geometry and only story-relevant props;
- single-panel compositions with clear emotion, deliberate negative space, and readable staging;
- charcoal/navy atmospheric gradients for tense interiors, warm cream/gold light for open scenes, and few accent colors.

The references include close-ups, medium conversations, wide group staging, over-the-shoulder views, action poses, and small comedic/chibi beats. Choose the shot for the story beat, but do not change the rendering language. Chibi proportions are allowed only when the manifest explicitly marks a comedic beat; never let them leak into adjacent normal scenes.

## Non-negotiable prompt order

The builder emits the final prompt in this order:

1. `STYLE LOCK`
2. immutable continuity rules
3. one `IDENTITY LOCK` and `WARDROBE LOCK` per visible character
4. `ENVIRONMENT LOCK`
5. current state and change from the previous frame
6. explicit hard-cut or continuous transition instruction
7. shot instruction
8. exclusions

Write `scenes[].image_prompt` only as a shot instruction: subject action, expression, framing, camera angle, screen direction, foreground/background relationship, and focal light. Do not restate or paraphrase appearance, costume, environment, or style. Duplicate descriptions drift.

## Reference approval

The first image pass creates a neutral reference image for every character and environment, plus `review/reference_contact_sheet.jpg`. Compare:

- face shape, apparent age, skin, eyes, hair silhouette, horns/ears/tail, body build;
- every default garment, closure, trim, accessory, and color;
- room geometry, horizon, doors/windows, fixed furniture, palette, and key light.

Only then copy `reference_digest` and `artifact_digest` from `review/reference_generation.json` to `review.approved_reference_digest` and `review.approved_reference_artifact_digest`, then set `references_approved` to `true`. Scene batch entries expose the relevant files as `reference_images` for provenance and reference-capable backends. The bundled Z-Image path does not use those multiple reference sheets as identity-conditioning inputs, so the sheet remains a comparison target rather than a claim of automatic identity transfer.

For a scene explicitly marked `continuous`, the bundled adapter does use the immediately preceding scene output with Diffusers' `ZImageImg2ImgPipeline`. Keep this for a true next beat in the same place. The bounded strength trades preservation against change: it can retain composition, palette, clothing, and low-level geometry, but it may also retain an unwanted error or resist a large pose/camera change. A hard cut is text-to-image and does not inherit pixels. Neither path guarantees character identity.

After scene generation, inspect `review/story_contact_sheet.jpg` in order. Reject any unexplained identity, wardrobe, prop, injury, weather, time, lighting, architecture, or screen-direction change. Copy `scene_digest` and `artifact_digest` from `review/scene_generation.json` to the matching approved fields and approve images only after all frames pass.

Do not reuse art after editing a card or scene. Generation-state files bind output art to its contract digest; the builder archives and regenerates stale outputs automatically.
