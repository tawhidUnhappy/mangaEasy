# mangaeasy/ocr — optional panel text OCR

Optional OCR that reads the text in panels and writes it into narration JSON as
an `ocr` field, so a narration-writing agent has the source dialogue/captions
to work from. Not required by the core pipeline.

## Files

- [`deepseek_ocr2_pipeline.py`](deepseek_ocr2_pipeline.py) — the pipeline that
  runs **inside the isolated `deepseek-ocr2` tool env** and adds an `ocr` field
  to each entry of the target narration JSON files. Driven by the
  `deepseek-ocr2` CLI command (dispatched via
  [`mangaeasy/tools/deepseek_ocr2.py`](../tools/deepseek_ocr2.py), which resolves
  the tool env and shells into it).

## Gotchas

- Needs the tool env: `mediaconductor install-tool deepseek-ocr2`. Like all external
  models it runs in its own `uv` env with pinned Torch/Transformers (see
  [`mangaeasy/tools/`](../tools/README.md)); this package only holds the
  in-env pipeline logic.
- `--force` replaces existing `ocr` fields; otherwise they're left as-is.
- Item selection uses the same `--items 01 02` / `--item-range` tokens as the
  rest of the CLI.
