"""Align canonical lyrics to ASR word timestamps and emit subtitle formats."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Mapping

TOKEN_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)

_SECTION_NAME = (
    r"(?:verse|pre[- ]?chorus|chorus|post[- ]?chorus|refrain|hook|bridge|"
    r"intro|outro|instrumental|interlude|breakdown)"
)
_SECTION_MARKER = (
    rf"\[\s*{_SECTION_NAME}(?:\s+(?:\d+|[ivxlcdm]+))?"
    rf"(?:\s*:\s*[^\[\]\r\n]+)?\s*\]"
)
STRUCTURAL_HEADING_RE = re.compile(
    rf"^{_SECTION_MARKER}(?:\s*/\s*{_SECTION_MARKER})*$",
    re.IGNORECASE,
)

DEFAULT_MINIMUM_CONFIDENCE = 0.72

DEFAULT_LYRICS_STYLE: dict[str, object] = {
    "preset": "edo-sky-fade-v1",
    "font_name": "Edo SZ",
    "font_file": "@bundled/edosz.ttf",
    "font_size_ratio": 0.058,
    "outline": 2.5,
    "shadow": 1.25,
    "fade_in_ms": 220,
    "fade_out_ms": 280,
    "alignment": 5,
    "margin_vertical_ratio": 0.08,
}


def normalize_token(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("’", "'")
    return "".join(char for char in value if char.isalnum() or char == "'").strip("'")


def lyric_lines(lyrics: str) -> list[tuple[str, list[str]]]:
    result = []
    for raw_line in lyrics.splitlines():
        line = raw_line.strip()
        if STRUCTURAL_HEADING_RE.fullmatch(line):
            continue
        tokens = TOKEN_RE.findall(line)
        if tokens:
            result.append((line, tokens))
    return result


def transcript_words(data: dict | list) -> list[dict]:
    if isinstance(data, list):
        raw = data
    else:
        raw = data.get("word_segments") or []
        if not raw:
            raw = [word for segment in data.get("segments", []) for word in segment.get("words", [])]
    words = []
    for entry in raw:
        text = str(entry.get("word") or entry.get("text") or "").strip()
        token = normalize_token(text)
        start, end = entry.get("start"), entry.get("end")
        if token and isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
            words.append({"word": text, "token": token, "start": float(start), "end": float(end)})
    return words


def _score(left: str, right: str) -> float:
    if left == right:
        return 3.0
    ratio = SequenceMatcher(None, left, right).ratio()
    return 1.0 if ratio >= 0.72 else -1.2


def align_tokens(canonical: list[str], observed: list[str]) -> tuple[list[int | None], float]:
    """Needleman-Wunsch alignment with a fuzzy substitution score."""
    rows, cols = len(canonical) + 1, len(observed) + 1
    gap = -1.0
    scores = [[0.0] * cols for _ in range(rows)]
    moves = [[""] * cols for _ in range(rows)]
    for i in range(1, rows):
        scores[i][0], moves[i][0] = i * gap, "up"
    for j in range(1, cols):
        scores[0][j], moves[0][j] = j * gap, "left"
    for i in range(1, rows):
        for j in range(1, cols):
            choices = (
                (scores[i - 1][j - 1] + _score(canonical[i - 1], observed[j - 1]), "diag"),
                (scores[i - 1][j] + gap, "up"),
                (scores[i][j - 1] + gap, "left"),
            )
            scores[i][j], moves[i][j] = max(choices, key=lambda item: item[0])
    mapping: list[int | None] = [None] * len(canonical)
    exact = 0
    i, j = len(canonical), len(observed)
    while i or j:
        move = moves[i][j]
        if move == "diag":
            i -= 1
            j -= 1
            mapping[i] = j
            exact += canonical[i] == observed[j]
        elif move == "up":
            i -= 1
        else:
            j -= 1
    confidence = exact / max(1, len(canonical))
    return mapping, confidence


def _interpolate(mapping: list[int | None], observed: list[dict]) -> list[tuple[float, float]]:
    if not observed:
        raise ValueError("transcript contains no timed words")
    timing: list[tuple[float, float] | None] = [None] * len(mapping)
    for index, observed_index in enumerate(mapping):
        if observed_index is not None:
            word = observed[observed_index]
            timing[index] = (word["start"], word["end"])
    for index, value in enumerate(timing):
        if value is not None:
            continue
        left = next((timing[pos] for pos in range(index - 1, -1, -1) if timing[pos] is not None), None)
        right = next((timing[pos] for pos in range(index + 1, len(timing)) if timing[pos] is not None), None)
        if left and right:
            span = max(0.08, right[0] - left[1])
            start = left[1] + span * 0.25
            end = min(right[0], start + max(0.08, span * 0.5))
        elif left:
            start, end = left[1], left[1] + 0.25
        elif right:
            start, end = max(0.0, right[0] - 0.25), right[0]
        else:
            start, end = 0.0, 0.25
        timing[index] = (start, max(start + 0.05, end))
    result: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in timing:  # type: ignore[misc]
        start = max(cursor, start)
        end = max(start + 0.05, end)
        result.append((start, end))
        cursor = start
    return result


def validate_minimum_confidence(value: object) -> float:
    """Return a finite confidence threshold in the inclusive 0..1 range."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("minimum_confidence must be a number from 0 to 1")
    confidence = float(value)
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("minimum_confidence must be a number from 0 to 1")
    return confidence


def align_lyrics(
    lyrics: str,
    transcript: dict | list,
    minimum_confidence: float = DEFAULT_MINIMUM_CONFIDENCE,
) -> dict:
    minimum_confidence = validate_minimum_confidence(minimum_confidence)
    lines = lyric_lines(lyrics)
    if not lines:
        raise ValueError("canonical lyrics contain no words")
    observed = transcript_words(transcript)
    canonical_words = [token for _line, tokens in lines for token in tokens]
    canonical = [normalize_token(token) for token in canonical_words]
    mapping, confidence = align_tokens(canonical, [word["token"] for word in observed])
    timings = _interpolate(mapping, observed)
    aligned_lines = []
    cursor = 0
    for number, (line, tokens) in enumerate(lines, start=1):
        line_timings = timings[cursor:cursor + len(tokens)]
        start = line_timings[0][0]
        end = max(line_timings[-1][1], start + 0.6)
        aligned_lines.append({"index": number, "text": line, "start": start, "end": end})
        cursor += len(tokens)
    unmatched = [canonical_words[i] for i, mapped in enumerate(mapping) if mapped is None]
    return {
        "schema_version": 1,
        "source": "canonical-lyrics-aligned-to-whisperx",
        "confidence": round(confidence, 4),
        "minimum_confidence": minimum_confidence,
        "review_required": confidence < minimum_confidence or bool(unmatched),
        "canonical_word_count": len(canonical),
        "transcript_word_count": len(observed),
        "unmatched_canonical_words": unmatched,
        "lines": aligned_lines,
    }


def _srt_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(aligned: dict, path: Path) -> None:
    blocks = []
    for line in aligned["lines"]:
        blocks.append(
            f"{line['index']}\n{_srt_time(line['start'])} --> {_srt_time(line['end'])}\n{line['text']}"
        )
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def _ass_time(seconds: float) -> str:
    centis = max(0, round(seconds * 100))
    hours, centis = divmod(centis, 360_000)
    minutes, centis = divmod(centis, 6000)
    secs, centis = divmod(centis, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def resolved_lyrics_style(style: Mapping[str, object] | None = None) -> dict[str, object]:
    """Return one complete, copy-safe lyric style with project overrides applied."""
    resolved = dict(DEFAULT_LYRICS_STYLE)
    if style:
        resolved.update(style)
    return resolved


def _ass_font_name(value: object) -> str:
    # Commas delimit ASS style fields, so never let a manifest value corrupt the
    # style row. Manifest validation reports the same issue before a build.
    return str(value or DEFAULT_LYRICS_STYLE["font_name"]).replace(",", " ").strip()


def _line_fades(line: dict, fade_in_ms: int, fade_out_ms: int) -> tuple[int, int]:
    """Keep both fades visible even for unusually short aligned lines."""
    duration_ms = max(1, round((float(line["end"]) - float(line["start"])) * 1000))
    requested = fade_in_ms + fade_out_ms
    if requested <= duration_ms or requested == 0:
        return fade_in_ms, fade_out_ms
    scale = duration_ms / requested
    return round(fade_in_ms * scale), round(fade_out_ms * scale)


def write_ass(
    aligned: dict,
    path: Path,
    width: int = 1920,
    height: int = 1080,
    lyrics_style: Mapping[str, object] | None = None,
) -> None:
    style = resolved_lyrics_style(lyrics_style)
    font_name = _ass_font_name(style["font_name"])
    font_size = max(24, round(height * float(style["font_size_ratio"])))
    margin = round(height * float(style["margin_vertical_ratio"]))
    outline = float(style["outline"])
    shadow = float(style["shadow"])
    alignment = int(style["alignment"])
    fade_in_ms = int(style["fade_in_ms"])
    fade_out_ms = int(style["fade_out_ms"])
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Lyrics,{font_name},{font_size},&H00FFFFFF,&H00FFFFFF,&H00202020,&H50000000,0,0,0,0,100,100,0,0,1,{outline:g},{shadow:g},{alignment},120,120,{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for line in aligned["lines"]:
        line_fade_in, line_fade_out = _line_fades(line, fade_in_ms, fade_out_ms)
        effect = rf"{{\fad({line_fade_in},{line_fade_out})}}"
        events.append(
            f"Dialogue: 0,{_ass_time(line['start'])},{_ass_time(line['end'])},"
            f"Lyrics,,0,0,0,,{effect}{_ass_escape(line['text'])}"
        )
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")


def write_alignment(
    aligned: dict,
    output_dir: Path,
    width: int = 1920,
    height: int = 1080,
    lyrics_style: Mapping[str, object] | None = None,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "timed_lyrics.json"
    srt_path = output_dir / "lyrics.srt"
    ass_path = output_dir / "lyrics.ass"
    json_path.write_text(json.dumps(aligned, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_srt(aligned, srt_path)
    write_ass(aligned, ass_path, width, height, lyrics_style)
    return {"json": json_path, "srt": srt_path, "ass": ass_path}
