# mangaeasy/video_pipeline — the item video pipeline (recommended workflow)

The **produce** stage: verified `panels/` + `narration.json` → per-item videos →
one joined long video with background music. This is the actively-developed
path; the older `mangaeasy/video/` + `mangaeasy/audio/` chapter commands are
legacy (being removed — see [legacy-inventory](../../docs/history/legacy-inventory.md)).

An "item" = one source unit (usually one chapter) that becomes one short video,
later joined into the long video. Item selection syntax across the CLI is
`--items 01 02 05-08` / `--item-range 01-12`.

## The one entry point

[`run_pipeline.py`](run_pipeline.py) (`mangaeasy video`) orchestrates the whole
build by shelling out to the narrower commands, in this order:

1. **Audio** — [`generate_audio.py`](generate_audio.py) (Kokoro, worker pool) or
   [`generate_audio_indextts.py`](generate_audio_indextts.py) (IndexTTS voice
   clone). `resolve_tts_engine()` picks IndexTTS only on a capable GPU machine.
2. **(opt) Fade** — [`preprocess_audio_fades.py`](preprocess_audio_fades.py).
3. **Render** — [`make_videos.py`](make_videos.py) /
   [`item_video_builder.py`](item_video_builder.py): one video per item,
   frame-aligned to audio.
4. **(opt) Join → normalize → BGM** — always three separate steps so re-mixing
   music doesn't re-join clips:
   [`make_long_video.py`](make_long_video.py) →
   [`normalize_long_audio.py`](normalize_long_audio.py) →
   [`add_long_video_bgm.py`](add_long_video_bgm.py) (+ [`music_bed.py`](music_bed.py) QC).

## Key shared modules

- [`common.py`](common.py) — roots/defaults, `item_dirs()`,
  `merge_item_selection()`, `expand_item_tokens()`, `chunk_list()` (shard for
  GPU workers + resume pruning). Other stages import selection helpers from here.
- [`item_assets.py`](item_assets.py) — **`load_narration(item_dir)` is the single
  source of truth** for reading narration (handles `intro.json`). Never re-parse
  `narration.json` elsewhere. Also `frame_aligned_duration()`.
- [`audio_takes.py`](audio_takes.py) — browse/restore archived audio takes
  (`audio-takes-list/restore`).

## Validation / cleanup commands

`video-check` ([check_items.py](check_items.py)), `narration-check`
([narration_check.py](narration_check.py) — structural narration validation
before audio: coverage, dangling images, empty text), `video-validate`
([validate_generation.py](validate_generation.py)), `video-audio-audit`
([audio_audit.py](audio_audit.py)), and the `video-clean-*` family (never touch
`library/` sources; generated output is archived, not deleted).

## Load-bearing invariants (guarded by tests — don't "optimize" away)

- Music mix uses `amix=…:normalize=0` and `alimiter=level=disabled`; BGM volume
  is **dB-native** (`--music-volume-db`, default −22). See
  [test_music_bed.py](../../tests/test_music_bed.py) and [CLAUDE.md](../../CLAUDE.md).
- `torch.backends.cudnn.benchmark` stays `False` in
  [`kokoro_batch_worker.py`](kokoro_batch_worker.py); `--gpu-workers` ≤ 4 on a
  3060.
- Resume pruning is **shard-aware** (`prune_recent_audio_for_resume(..., shards=)`).

## Tests

`tests/test_item_selection.py`, `test_narration_loading.py`, `test_archive.py`,
`test_resume_pruning.py`, `test_music_bed.py`, `test_long_video_discovery.py`,
`test_e2e_render.py`.
