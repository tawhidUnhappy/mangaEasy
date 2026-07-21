# mangaeasy.audio — TTS generation internals

- `tts_pipeline.py` — the batch IndexTTS2 worker. **Runs inside the external
  `index-tts` tool env** (launched by `mediaconductor video-audio-indextts`, which
  sets `INDEX_TTS_ROOT` and the env's Python) — never import it from app
  code; it imports `indextts` at module scope and exits if that fails.
- `emotion.py` — the narration `"emotion"` field contract: validation
  (`narration_emotion`, `emotion_lint`), the suggested vocabulary, and the
  mapping to IndexTTS2 `emo_text`/`emo_alpha` kwargs (`indextts_kwargs`).
  `emotion_lint` accepts only `calm`, `neutral`, `slightly sad`, and `slightly
  happy`; all other hints are ignored by TTS and rejected by `work-qa`.
  `narration_delivery_lint` blocks phonetic laughs/vocal noises ("ghaha", "ha
  ha ha", "aaaargh"), exclamation marks, and shout-like all-caps instead of
  allowing TTS to perform them loudly. Deliberately
  import-light (no torch/indextts): `work-qa`, prompt docs, and tests use it
  outside the TTS env. Audio generation and rendering also run the central
  calm-narration preflight before doing expensive work. Kokoro ignores valid
  emotion fields — the narration schema stays engine-portable.
