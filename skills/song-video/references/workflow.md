# Song Video stages

1. Initialize `song.json`; keep supplied lyrics exactly as the canonical source.
2. Review ACE-Step prompt, language, BPM/duration, seed, and vocal consent. A supplied audio path skips generation.
3. Generate with pinned ACE-Step 1.5 into its isolated uv project.
4. Separate vocals with the maintained Demucs fork and explicit Hugging Face model. Use the full mix—not stems—as the final video's audio.
5. Transcribe the vocal stem with WhisperX. Align its timed tokens to canonical lyrics; preserve line breaks and save raw evidence.
   The default setup includes a pinned offline English Wav2Vec2 aligner. Non-English manifests are rejected until a reviewed, immutable language-specific CTC extension is installed; never let a production run select a floating model.
6. Review low-confidence/missing tokens, choruses, contractions, punctuation, multilingual words, and instrumental gaps. Structural markers such as `[Verse]` and `[Chorus]` remain useful to ACE-Step but are not rendered as lyric lines. Keep displayed words identical to `song.json`; edit timing only in `timed_lyrics.json`, then bind approval to its reported digest.
7. Generate the default minimalistic-sky background. Keep it text-free, uncluttered, and dark or calm enough for white lyrics; edit the manifest prompt if the song needs another palette.
8. Render the centered `edo-sky-fade-v1` ASS style: Edo SZ, a restrained outline, small shadow, and per-line fades. The builder recreates SRT/ASS from the approved timing JSON on every render and binds audio, visual, timing, style, font, and output hashes. Review the complete video and approve its exact SHA-256.
9. Set `youtube.profile` to the exact profile/channel verified with `youtube-profiles` and `youtube-status --profile <name> --verify`. Confirm rights/provenance and synthetic-media disclosure. Publish once; `publish.json` records the selected account/result and prevents accidental duplicate upload.

Each heavy tool has its own uv environment under the managed tools directory. Do not install ACE-Step, Demucs, WhisperX, or Z-Image into the core environment.
