# mangaeasy/panels — cropping raw pages into panels

The **crop** stage of the pipeline. Turns raw source images
(`library/<Project>/<item>/download/`) into ordered panel crops
(`.../panels/`) plus verification images a human/AI clears before narration.

Start with the operator doc: [docs/operate/crop-verify-narrate.md](../../docs/operate/crop-verify-narrate.md).

## Two crop paths (pick by source shape)

| File | Command | For | How |
|---|---|---|---|
| [`style_detect.py`](style_detect.py) | `style-detect` | choosing between the two | aspect-ratio heuristic over `download/` pages → `webtoon`/`paged`/`uncertain` verdict + sample images to confirm visually |
| [`webtoon.py`](webtoon.py) | `webtoon-split` | vertical strips (webtoons) | stitch → gutter split → auto-split tall panels → rescue content gaps → verify sheets |
| [`page.py`](page.py) | `page-split` | paged manga | MAGI v3 detect (once/chapter) → reading-order → crop → verify overlays |
| [`gutter.py`](gutter.py) | `gutter-split` | low-level page/gutter split | the gutter engine `webtoon.py` builds on (`_recursive_ranges`, `collect_image_paths`, `stitch_images`) |
| [`ai.py`](ai.py) | *(library)* | MAGI detection + reading order | `detect_panels_ai()`, `_manga_reading_order(boxes, rtl)`, `_clamp_box()` |

## Public entry points

- `webtoon.main()` / `page.main()` — the two `*-split` commands. Both take
  `--project-root library/<name> --items 01 02` (or `--item-range`), write
  crops to `<item>/panels/`, archive any existing `panels/` to
  `<item>/old/run_NNNN/` first, and emit `MANGAEASY_PROGRESS` + `MANGAEASY_RESULT`.
- `ai.detect_panels_ai(image_path)` — MAGI boxes for one image, reading-order
  sorted. `page.py` uses the batched adapter instead (model loaded once).
- `ai._manga_reading_order(boxes, rtl=None)` — the shared RTL/LTR band-overlap
  topological sort. Both crop paths and any new panel-ordering code use this;
  don't re-implement it.

## Gotchas

- **MAGI runs in its own tool env**, not the main env (transformers version
  conflict). `page.py` shells out to `assets/tools/batch_detect_magi.py` via
  the `magi-v3` env python. Install it with `mangaeasy install-tool magi-v3`.
  The env pins (transformers 4.48.3, `attn_implementation="eager"`) are baked
  into that script — see [CLAUDE.md](../../CLAUDE.md) / the magi-v3 memory.
- **Never trust detector boxes.** Both commands emit verify images
  (`work/webtoon_verify/…`, `work/page_verify/…`) precisely so wrong crops are
  caught before narration. `--overrides` is the correction escape hatch for
  both (formats differ — webtoon = range ops on the stitched strip; page =
  per-page pixel boxes).
- **Reading direction matters**: `rtl` for Japanese, `ltr` for Chinese/Korean.
  `page-split` takes `--reading-direction`; the default reads
  `cut_page.reading_direction` from system config.

## Tests

[tests/test_webtoon_split.py](../../tests/test_webtoon_split.py),
[tests/test_page_split.py](../../tests/test_page_split.py) (pure logic — no GPU).
