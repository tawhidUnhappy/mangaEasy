#!/usr/bin/env python3
"""mangaeasy.audio.fade
Create faded COPIES of generated WAV files without touching the originals.

Source : manga/{name}/{ch:02d}/audio/*.wav
Output : manga/{name}/{ch:02d}/tmp/audio_faded/*.wav

Audio settings come from config.system.json → audio section.
"""

from __future__ import annotations

from pathlib import Path

from pydub import AudioSegment

from mangaeasy.config import load_download_config, load_system_config
from mangaeasy.paths import audio_dir, faded_audio_dir


def _audio_cfg() -> dict:
    return load_system_config().get("audio", {})


def process_one(src: Path, dst: Path) -> None:
    cfg = _audio_cfg()

    fade_in_ms      = int(cfg.get("fade_in_ms",      10))
    fade_out_ms     = int(cfg.get("fade_out_ms",      120))
    tail_silence_ms = int(cfg.get("tail_silence_ms",  20))
    sample_rate     = int(cfg.get("sample_rate",      48000))
    channels        = int(cfg.get("channels",         1))
    sample_width    = 2  # always 16-bit PCM

    audio    = AudioSegment.from_file(src)
    fade_in  = min(fade_in_ms,  len(audio))
    fade_out = min(fade_out_ms, len(audio))
    processed = audio.fade_in(fade_in).fade_out(fade_out)
    if tail_silence_ms > 0:
        processed += AudioSegment.silent(duration=tail_silence_ms)
    processed = (
        processed
        .set_frame_rate(sample_rate)
        .set_channels(channels)
        .set_sample_width(sample_width)
    )
    tmp = dst.with_suffix(".tmp.wav")
    processed.export(tmp, format="wav")
    tmp.replace(dst)


def main() -> None:
    dl      = load_download_config()
    name    = str(dl["name"])
    chapter = int(dl["chapter"])

    raw_dir   = audio_dir(name, chapter)
    faded_dir = faded_audio_dir(name, chapter)

    if not raw_dir.exists():
        raise SystemExit(f"[FATAL] Raw audio directory not found: {raw_dir}")

    wav_files = sorted(raw_dir.glob("*.wav"))
    if not wav_files:
        print("[INFO] No WAV files found. Nothing to fade.")
        return

    faded_dir.mkdir(parents=True, exist_ok=True)

    cfg = _audio_cfg()
    print(f"[INFO] Manga: {name}  Chapter: {chapter:02d}")
    print(f"[INFO] Fade in: {cfg.get('fade_in_ms', 10)} ms  "
          f"Fade out: {cfg.get('fade_out_ms', 120)} ms  "
          f"Tail: {cfg.get('tail_silence_ms', 20)} ms")
    print(f"[INFO] Originals kept in : {raw_dir}")
    print(f"[INFO] Faded copies in   : {faded_dir}")

    for wav in wav_files:
        out = faded_dir / wav.name
        process_one(wav, out)
        print(f"[OK] {wav.name}")

    print("[DONE] Faded copies created.")


if __name__ == "__main__":
    main()
