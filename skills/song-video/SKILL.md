---
name: song-video
description: Generate or ingest a song and produce a correctly timed YouTube lyrics video with MediaConductor. Use for ACE-Step song generation, Demucs vocal separation, WhisperX timing, canonical lyric reconciliation, minimalistic-sky visuals, lyrics rendering, QA, rights review, and explicit YouTube upload.
---

# Song & Lyrics Video

Read [references/workflow.md](references/workflow.md) and [references/lyric-style.md](references/lyric-style.md). Operate only this catalog:

Use `<mc>` from the router: the source-checkout invocation, globally installed
`mediaconductor`, or the absolute frozen executable. If this skill was loaded
directly, select that form now. Projects may live anywhere allowed by the MCP
server's `--allow-root` policy.

```bash
<mc> setup --mode song-video
<mc> doctor --mode song-video --json
<mc> commands --mode song-video --json --full
```

Use `song-init` with supplied canonical lyrics and either a source audio file or a music prompt. The canonical lyrics are the content authority: Demucs isolates vocals and WhisperX supplies timing evidence, but ASR text must never silently replace user lyrics.

Run `<mc> song-build --manifest <absolute-song.json> --stage all` as a background job. Exit code 3 is a deliberate resumable gate. At the first gate, inspect `alignment/timed_lyrics.json`, raw transcript, unmatched words, confidence, and repeated choruses. Correct canonical words in `song.json` and rerun alignment; edit only timings in `timed_lyrics.json`. Then set `alignment.approved=true` and copy the `alignment_digest` reported by `song-check --json` to `alignment.approved_digest`. Rerun `--stage all`; it creates the “minimalistic sky” visual, rebuilds SRT/ASS from the reviewed JSON, renders with `edo-sky-fade-v1`, and stops again. Watch the complete video, then copy `review/video_generation.json.sha256` to `review.approved_video_sha256` and set `review.video_approved=true`.

For MCP, call `job_start` with `{"tool":"song_build","arguments":{"manifest":"D:/absolute/project/song.json","stage":"all"}}`, then poll `job_status`. A shell-only agent can use `<mc> job-start --tool song_build --arguments-json '{"manifest":"D:/absolute/project/song.json","stage":"all"}'`.

Publishing is never implicit. Use `<mc> song-check --manifest <absolute-song.json> --for-publish`, inspect audio/video, confirm lyrics/audio/voice rights and synthetic-media disclosure, then use `<mc> song-build --manifest <absolute-song.json> --stage publish` only after explicit user approval. First read the shared [`youtube-publishing.md`](../media-conductor/references/youtube-publishing.md), verify the intended named profile/channel, and carry that exact selection through the publish stage.
