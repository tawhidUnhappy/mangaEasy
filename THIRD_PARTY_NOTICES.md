# Third-party software notices

mangaEasy is MIT-licensed (see [LICENSE](LICENSE)). It builds on, bundles, or
downloads the following third-party software. Each project is governed by its
own license.

## Bundled in the desktop app

- **Electron / Chromium / Node.js** — MIT and various
  (https://github.com/electron/electron)
- **xterm.js** (`@xterm/xterm`) — MIT
- **Monaco Editor** — MIT
- **node-pty** — MIT (Microsoft)
- **React** — MIT
- **Python** (via PyInstaller-frozen backend) — PSF License
- **PyInstaller** bootloader — GPL with a special exception permitting
  distribution of bundled applications under any license
- Python libraries listed in `pyproject.toml` (requests, Flask, Pillow,
  pydub, numpy, BeautifulSoup4, …) — MIT/BSD/Apache-style licenses

## Downloaded on demand into the app's own data folder

These are fetched from their official sources on first use ("Download core
tools" in Setup, or `mangaeasy install-tool`), not distributed with mangaEasy:

- **FFmpeg / ffprobe** — GPL builds from
  https://github.com/BtbN/FFmpeg-Builds (Windows/Linux) and
  https://ffmpeg.martin-riedl.de (macOS). FFmpeg is licensed under the
  GPL/LGPL; source code is available from https://ffmpeg.org and the build
  pages above.
- **uv / uvx** — MIT/Apache-2.0 (https://github.com/astral-sh/uv)
- **git-lfs** — MIT (https://github.com/git-lfs/git-lfs)
- **Node.js** (dev checkouts only, for building the desktop app) — MIT
- **Kokoro-82M** TTS — model and code under their respective licenses
  (https://huggingface.co/hexgrad/Kokoro-82M, Apache-2.0)
- **IndexTTS** — https://github.com/index-tts/index-tts (see repo license)
- **MAGI v3** panel detection / **DeepSeek-OCR 2** — research models from
  Hugging Face; see their model cards for license terms

If you redistribute mangaEasy or a derivative, review these licenses —
especially FFmpeg's — for your own compliance obligations.
