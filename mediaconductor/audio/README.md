# mangaeasy.audio — TTS generation internals

- `tts_pipeline.py` — the batch IndexTTS2 worker. **Runs inside the external
  `index-tts` tool env** (launched by `mediaconductor video-audio-indextts`, which
  sets `INDEX_TTS_ROOT` and the env's Python) — never import it from app
  code; it imports `indextts` at module scope and exits if that fails.
- `emotion.py` — the narration `"emotion"` field contract: validation
  (`narration_emotion`, `emotion_lint`), the suggested vocabulary, and the
  mapping to IndexTTS2 `emo_text`/`emo_alpha` kwargs (`indextts_kwargs`).
  `emotion_lint` rejects scream/shout-intensity words (`SCREAM_TERMS`) —
  IndexTTS2 renders those as actual screaming, not a natural narrator voice.
  `narration_delivery_lint` flags narration text that spells out a laugh or
  scream phonetically ("ha ha ha", "aaaargh") instead of describing it in
  prose — TTS can't pronounce those, only real words. Deliberately
  import-light (no torch/indextts): `work-qa`, prompt docs, and tests use it
  outside the TTS env. Kokoro ignores emotion fields — the narration schema
  stays engine-portable.
