# Song Video package

`workflow.py` owns the versioned `song.json` contract and stage orchestration. `lyrics.py` aligns canonical supplied lyrics to WhisperX timing evidence and writes JSON/SRT/ASS. ACE-Step, Demucs, WhisperX, and Z-Image execute only in their isolated uv environments.

New manifests use the `edo-sky-fade-v1` lyric treatment: centered white Edo SZ lettering, a restrained dark outline and small shadow, and per-line fade-in/fade-out over a minimalistic-sky background. The exact values live under `render.lyrics_style`, so a project can tune them without editing Python.

The default `@bundled/edosz.ttf` token resolves to the packaged Edo SZ asset and its directory is passed to FFmpeg/libass. See `THIRD_PARTY_NOTICES.md` and `mangaeasy/assets/fonts/README.md` for provenance and license terms. A project may instead set `font_file` to a licensed `.ttf`, `.otf`, or `.ttc` path; relative paths resolve from the song project. If it is `null`, libass looks for `font_name` in the host's installed fonts and may substitute another face. The render stage rebuilds ASS from the reviewed timing JSON after a style change and invalidates the prior video approval.
