# Thumbnail Guide

Use **Z-Image Turbo** for generated manga/manhwa recap thumbnails. It installs
as an isolated tool and auto-selects the best strategy for the detected
hardware (CUDA, Apple Silicon MPS, or CPU):

```bash
mangaeasy install-tool z-image-turbo
mangaeasy zimage --prompt-file thumb_prompt.txt --output thumbnail_base.png \
    --width 1280 --height 720 --count 4
```

For the channel style, write a platform-safe "gooner thumbnail" prompt: glossy
anime/manhwa key art, visibly adult characters, fanservice energy, exaggerated
expressions, dramatic camera angle, and a clear emotional hook. Keep it
YouTube-safe:

- Every character must be visibly adult and fully clothed.
- Suggestive fanservice is the ceiling: no nudity, transparent clothing,
  explicit pose, sexual act, or minor-coded character.
- Use the video's actual characters, outfits, power visuals, and setting
  instead of a generic template.
- Use two- or three-character tension: one shocked/flustered/blushing, one
  calm/smug/powered-up.
- Do not put text, logos, or watermarks in the generated image; add those
  afterward with PIL or an editor.

Prompt shape:

```text
glossy anime/manhwa key art, visibly adult [character A] blushing with wide
sparkling eyes and a flustered expression, fully clothed form-fitting [story
outfit], beside visibly adult [character B] calm and confident with a faint
smirk, [actual story setting] background softly blurred, dramatic low-angle
shot, saturated cyan and gold lighting, highly detailed, cinematic, no text,
no logo, no nudity, no transparent clothing
```

After generation, pick the best of four variants and add thumbnail furniture:
1-3 short text blocks, black stroke, yellow/white fills, speech-tail triangles,
one arrow if it clarifies the reveal, and a thin white inset border. Always
inspect the final image at full size before upload, especially faces/hands,
cropped speech bubbles, and anything that could read as explicit or minor-coded.

For the full production workflow, see
[recap-video-playbook.md](recap-video-playbook.md#phase-9--thumbnail-1280720).
