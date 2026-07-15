# Lyric visual contract

Use a clean, music-channel presentation inspired by the requested 7clouds-like treatment without copying channel branding, logos, or thumbnails.

## Background prompt

Keep `visual.prompt` anchored on `minimalistic sky`: a restrained sky gradient, sparse soft clouds, subtle atmospheric light, cinematic 16:9 framing, generous negative space, and no generated text, logo, watermark, people, buildings, or busy foreground. Choose a palette that keeps white lyrics readable throughout the frame.

## Subtitle defaults

`render.lyrics_style` is the source of truth. New manifests use:

```json
{
  "preset": "edo-sky-fade-v1",
  "font_name": "Edo SZ",
  "font_file": "@bundled/edosz.ttf",
  "font_size_ratio": 0.058,
  "outline": 2.5,
  "shadow": 1.25,
  "fade_in_ms": 220,
  "fade_out_ms": 280,
  "alignment": 5,
  "margin_vertical_ratio": 0.08
}
```

- Keep the lyrics centered (`alignment: 5`) and white. The dark outline protects legibility; the smaller shadow adds depth without looking heavy.
- Each canonical line is one ASS event. `fade_in_ms` and `fade_out_ms` become a `\fad(...)` override; short events are scaled so both fades fit.
- `font_size_ratio` and vertical margin scale from video height, so the preset remains usable at 720p, 1080p, and 4K.
- Preserve canonical spelling and line breaks. WhisperX is timing evidence only.

## Font reproducibility

The `@bundled/edosz.ttf` token resolves to MediaConductor's packaged Edo SZ asset, so a default install renders repeatably without a system-font dependency. Read `THIRD_PARTY_NOTICES.md` and `mangaeasy/assets/fonts/README.md` before distribution. The font's internal family name matches `font_name`, and FFmpeg/libass receives its containing directory through `fontsdir`.

A project may instead point `font_file` at another licensed `.ttf`, `.otf`, or `.ttc`; a relative path resolves from the directory containing `song.json`. Update `font_name` to that file's internal family name.

If `font_file` is `null`, the render host must already have the named font installed. Treat the validation warning as a portability warning and review a frame to catch font fallback. Never download a font from an unverified mirror or commit it without its license.

After any style change, rerun the render/all stage. The builder regenerates `alignment/lyrics.ass` from the approved canonical timing JSON, invalidates the prior video hash, and requires a new visual review; ASR does not need to run again unless lyrics, vocals, language, or the confidence threshold changed.
