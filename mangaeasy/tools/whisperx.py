"""Transcribe vocals with WhisperX, then time canonical supplied lyrics."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from mangaeasy.brand import CLI_NAME
from mangaeasy.runtime import popen_kwargs
from mangaeasy.song.lyrics import (
    DEFAULT_LYRICS_STYLE,
    DEFAULT_MINIMUM_CONFIDENCE,
    align_lyrics,
    validate_minimum_confidence,
    write_alignment,
)
from mangaeasy.tools.external import python_command, resolve_tool_dir, tool_env
from mangaeasy.utils import emit_result


def _minimum_confidence_arg(value: str) -> float:
    try:
        return validate_minimum_confidence(float(value))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "minimum confidence must be a number from 0 to 1"
        ) from exc


def main() -> int:
    parser = argparse.ArgumentParser(prog=f"{CLI_NAME} whisperx")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--lyrics-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--transcript-json", type=Path,
                        help="Reuse raw WhisperX JSON instead of transcribing (review/testing).")
    parser.add_argument("--language")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument(
        "--minimum-confidence",
        type=_minimum_confidence_arg,
        default=DEFAULT_MINIMUM_CONFIDENCE,
        help="Require canonical-word alignment confidence from 0 to 1 (default: 0.72).",
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--font-name", default=DEFAULT_LYRICS_STYLE["font_name"])
    parser.add_argument("--font-size-ratio", type=float, default=DEFAULT_LYRICS_STYLE["font_size_ratio"])
    parser.add_argument("--outline", type=float, default=DEFAULT_LYRICS_STYLE["outline"])
    parser.add_argument("--shadow", type=float, default=DEFAULT_LYRICS_STYLE["shadow"])
    parser.add_argument("--fade-in-ms", type=int, default=DEFAULT_LYRICS_STYLE["fade_in_ms"])
    parser.add_argument("--fade-out-ms", type=int, default=DEFAULT_LYRICS_STYLE["fade_out_ms"])
    parser.add_argument("--alignment", type=int, choices=range(1, 10), default=DEFAULT_LYRICS_STYLE["alignment"])
    parser.add_argument(
        "--margin-vertical-ratio",
        type=float,
        default=DEFAULT_LYRICS_STYLE["margin_vertical_ratio"],
    )
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    raw_path = args.transcript_json.resolve() if args.transcript_json else output_dir / "whisperx_raw.json"
    if not args.transcript_json:
        tool_dir = resolve_tool_dir("whisperx", required=False)
        if tool_dir is None:
            print(f"[error] WhisperX is not installed. Run: {CLI_NAME} install-tool whisperx")
            return 1
        adapter = tool_dir / "transcribe_whisperx.py"
        model = tool_dir / "models" / "faster-whisper-large-v3"
        align_model = tool_dir / "models" / "wav2vec2-base-960h"
        command = [
            *python_command(tool_dir), str(adapter), "--audio", str(args.audio.resolve()),
            "--output", str(raw_path), "--model", str(model),
            "--align-model", str(align_model), "--device", args.device,
            "--minimum-confidence", str(args.minimum_confidence),
        ]
        if args.language:
            command += ["--language", args.language]
        rc = subprocess.run(command, cwd=tool_dir, env=tool_env(), **popen_kwargs()).returncode
        if rc:
            return rc
    try:
        transcript = json.loads(raw_path.read_text(encoding="utf-8"))
        lyrics = args.lyrics_file.read_text(encoding="utf-8")
        aligned = align_lyrics(lyrics, transcript, args.minimum_confidence)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[error] lyrics alignment failed: {exc}")
        return 1
    lyrics_style = {
        "font_name": args.font_name,
        "font_size_ratio": args.font_size_ratio,
        "outline": args.outline,
        "shadow": args.shadow,
        "fade_in_ms": args.fade_in_ms,
        "fade_out_ms": args.fade_out_ms,
        "alignment": args.alignment,
        "margin_vertical_ratio": args.margin_vertical_ratio,
    }
    outputs = write_alignment(aligned, output_dir, args.width, args.height, lyrics_style)
    emit_result(outputs=list(outputs.values()), raw_transcript=raw_path,
                confidence=aligned["confidence"], review_required=aligned["review_required"])
    return 0 if not aligned["review_required"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
