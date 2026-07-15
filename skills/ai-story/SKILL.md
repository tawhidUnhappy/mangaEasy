---
name: ai-story
description: Turn a written story into a continuity-checked narrated AI story video with MediaConductor. Use when an AI must author or adapt a story, define reusable character/location anchors, write consistent scene image prompts and narration, generate images/audio, review continuity, render, and explicitly publish to YouTube.
---

# AI Story Video

Read [references/art-style.md](references/art-style.md), then [references/manifest.md](references/manifest.md). Operate only this catalog:

Use `<mc>` from the router: the source-checkout invocation, globally installed
`mediaconductor`, or the absolute frozen executable. If this skill was loaded
directly, select that form now. Projects may live anywhere allowed by the MCP
server's `--allow-root` policy.

```bash
<mc> setup --mode ai-story
<mc> doctor --mode ai-story --json
<mc> commands --mode ai-story --json --full
```

Create the project with `story-init`. Complete every immutable character card, environment card, and ordered scene-state ledger before `story-check`. Never weaken or delete the generated style contract. Keep each scene prompt focused on action/composition; the builder deterministically injects hashed style, identity, wardrobe, environment, state, and negative locks.

Run `<mc> story-build --manifest <absolute-story.json> --stage images` as a background job. The first pass generates identity/environment references and stops. Inspect `review/reference_contact_sheet.jpg`; copy both `reference_digest` and `artifact_digest` from `review/reference_generation.json` into the matching `review.approved_*` fields, then approve it. Run the image stage again to generate scenes. Inspect `review/story_contact_sheet.jpg` in sequence, correct every unexplained drift, then copy both digests from `review/scene_generation.json` and approve the current frames. Contract digests detect prompt/card edits; artifact digests detect regenerated or replaced files.

For MCP, call `job_start` with `{"tool":"story_build","arguments":{"manifest":"D:/absolute/project/story.json","stage":"images"}}`, then poll `job_status`. A shell-only agent can use `<mc> job-start --tool story_build --arguments-json '{"manifest":"D:/absolute/project/story.json","stage":"images"}'`.

Mark every scene transition explicitly. Use `hard-cut` for scene one and whenever the location, time, camera setup, or cast changes substantially. Use `continuous` only for a true next beat in the same location; the included Z-Image adapter then feeds the immediately previous scene output to the official img2img pipeline at the manifest's bounded strength. Downstream continuous frames regenerate whenever their init frame regenerates.

Character/location reference sheets remain human/agent QA targets, not multi-reference conditioning inputs. Previous-frame img2img can preserve layout, palette, pose, costume, and other low-level structure, but it can also resist a large action change or propagate an error. Never claim guaranteed identity transfer. Consistency still requires exact prompt locks, deterministic seeds, an explicit state ledger, targeted regeneration, and both approval gates.

Run `<mc> story-build --manifest <absolute-story.json> --stage video`. It stops after recording the exact output in `review/video_generation.json`; inspect the complete video, then set `review.video_approved=true` and copy its `sha256` to `review.approved_video_sha256`. The video record binds FPS, requested and resolved TTS, narration, speaker/emotion fields, scene artifacts, and any IndexTTS speaker-WAV content. Keep that voice reference unchanged and available through publication. Run `story-check --manifest <absolute-story.json> --for-publish --json` before upload. Publishing is never part of `--stage all`; use `--stage publish` only after explicit user approval, rights/voice confirmations, and synthetic-media disclosure acknowledgement. First read the shared [`youtube-publishing.md`](../media-conductor/references/youtube-publishing.md), verify the intended named profile/channel, and carry that exact selection through the publish stage.
