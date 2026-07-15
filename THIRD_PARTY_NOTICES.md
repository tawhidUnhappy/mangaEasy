# Third-party software notices

MediaConductor is MIT-licensed; see [LICENSE](LICENSE). The project distributes
or can install the components below. Each component remains subject to its own
license and terms. This notice is a practical inventory, not legal advice and
not a replacement for the upstream license files.

## Distributed with MediaConductor

- **Edo SZ** by Vic Fieger is distributed as
  `mangaeasy/assets/fonts/edosz.ttf`. The author labels the font "100% free"
  and permits personal, commercial, and charitable design use as well as
  inclusion in software; selling the font itself is prohibited. See the
  [font page](https://www.dafont.com/edo-sz.font) and the
  [author's licensing FAQ](https://vicfieger.com/~font/faq.html). The bundled
  file's SHA-256 is
  `BC67CF1C852C6D4FFBB7BC8FB4CD702D293EE49BDA3DE12C94E3635D80A4D55B`.
- **Python runtime dependencies** are declared in `pyproject.toml`: Requests,
  Pillow, img2pdf, NumPy, pywinpty (Windows), google-auth, and
  google-auth-oauthlib. Source distributions and wheels contain their own
  metadata and license references.
- **PyInstaller** is used only for frozen release builds. Its bootloader has a
  licensing exception that permits bundling applications under other licenses.
  Python itself is covered by the PSF License.

MediaConductor does not distribute model weights, voice samples, generated
music, or the user's source media in its Git repository or Python package.

## Installed on demand in isolated environments

The following projects are fetched from their official repositories or Hugging
Face model pages only when the user runs `mediaconductor setup`,
`mediaconductor bootstrap-tools`, or `mediaconductor install-tool`. They are
not part of the MediaConductor wheel or frozen release archive.

- **FFmpeg / ffprobe** — builds from
  [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) on Windows and
  Linux and [Martin Riedl's builds](https://ffmpeg.martin-riedl.de/) on macOS.
  Available features and GPL/LGPL obligations depend on the selected build;
  source is available from [FFmpeg](https://ffmpeg.org/).
- **uv / uvx 0.11.16** — Apache-2.0 or MIT, from
  [astral-sh/uv](https://github.com/astral-sh/uv). Bootstrap archives are
  version-pinned and checked against embedded upstream SHA-256 values.
- **Git LFS 3.7.1** — MIT, from
  [git-lfs/git-lfs](https://github.com/git-lfs/git-lfs). Bootstrap archives are
  version-pinned and checked against embedded upstream SHA-256 values.
- **ACE-Step 1.5** song generation — source from
  [ace-step/ACE-Step-1.5](https://github.com/ace-step/ACE-Step-1.5) and weights
  from [ACE-Step/Ace-Step1.5](https://huggingface.co/ACE-Step/Ace-Step1.5).
- **Demucs / HTDemucs-ft** vocal separation — maintained source from
  [adefossez/demucs](https://github.com/adefossez/demucs) and weights from
  [adefossez/HTDemucs-ft](https://huggingface.co/adefossez/HTDemucs-ft).
- **WhisperX / faster-whisper-large-v3** lyric timing — source from
  [m-bain/whisperX](https://github.com/m-bain/whisperX) and weights from
  [Systran/faster-whisper-large-v3](https://huggingface.co/Systran/faster-whisper-large-v3).
  The default English forced aligner is
  [facebook/wav2vec2-base-960h](https://huggingface.co/facebook/wav2vec2-base-960h)
  (Apache-2.0), pinned and downloaded from Hugging Face.
- **IndexTTS 2** voice synthesis — source from
  [index-tts/index-tts](https://github.com/index-tts/index-tts) and weights from
  [IndexTeam/IndexTTS-2](https://huggingface.co/IndexTeam/IndexTTS-2).
- **Kokoro 82M** speech synthesis — source from
  [hexgrad/kokoro](https://github.com/hexgrad/kokoro) and its referenced model
  files on Hugging Face.
- **MAGI v3** panel detection — source from
  [ragavsachdeva/magi](https://github.com/ragavsachdeva/magi) and the model
  page selected by the upstream adapter.
- **DeepSeek-OCR 2** — source and model from
  [deepseek-ai/DeepSeek-OCR-2](https://huggingface.co/deepseek-ai/DeepSeek-OCR-2).
- **Z-Image Turbo** image generation — model from
  [Tongyi-MAI/Z-Image-Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo).
- **Faster Whisper** optional transcription — source from
  [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper); the user
  selects and downloads model weights at runtime.

Before redistributing a prepared tool environment, model cache, frozen build,
or derivative application, review the exact upstream revisions and licenses
for every included component. In particular, model licenses and acceptable-use
terms can differ from the source-code license.
