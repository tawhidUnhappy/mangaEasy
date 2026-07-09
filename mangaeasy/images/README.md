# mangaeasy/images — shared image operations

Utility image commands used across the pipeline: format conversion, PDF export,
watermarking, and packing panels for AI context. None of these are on the
critical crop→narrate→video path; they're helpers.

## Files

| File | Command | Role |
|---|---|---|
| [`ai_zip.py`](ai_zip.py) / [`ai_zip_cli.py`](ai_zip_cli.py) | `ai-zip` | pack a chapter's panels into a labelled ZIP so an LLM can read them as context (`panels_to_ai_zip`) |
| [`convert.py`](convert.py) | `convert-images` | normalize/convert image formats |
| [`pdf.py`](pdf.py) | `to-pdf` | export chapter images to a PDF |
| [`pdf_lossless.py`](pdf_lossless.py) | `to-pdf-lossless` | lossless PDF export |
| [`thumbnail_compose.py`](thumbnail_compose.py) | `thumbnail-compose` | text furniture onto a thumbnail base: stroked blocks, optional arrow, inset border ([docs/thumbnail.md](../../docs/thumbnail.md)) |
| [`watermark.py`](watermark.py) / [`watermark_util.py`](watermark_util.py) | `watermark` | burn a text watermark onto images |

## Entry points

Each command module exposes `main()` (its own `argparse`). `ai_zip.panels_to_ai_zip(panels_dir, output, log, progress)` is the reusable core behind `ai-zip`.

## Notes

- These read the chapter/panel layout via `mangaeasy.paths` helpers
  (`panels_dir`, `chapter_dir`, `download_dir`).
- Watermarking here is for arbitrary image sets; the recap **thumbnail** text
  furniture is a separate concern documented in
  [docs/thumbnail.md](../../docs/thumbnail.md).
