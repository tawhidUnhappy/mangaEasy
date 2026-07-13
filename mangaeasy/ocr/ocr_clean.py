"""Post-filter for DeepSeek-OCR output before it lands in transcript.json.

The model is a strong bubble reader but a messy generator: on textless or
art-only panels it emits Chinese "no recognizable text" placeholders, English
apology/scene-description hallucinations, fake markdown ``<table>`` dumps
(seen at ~10K tokens for one panel), LaTeX fragments, and long repeated-
character runs. All of that used to be stored verbatim — burning the
narration writer's context window and occasionally misleading it.

``clean_ocr_text`` keeps real bubble/caption text (line structure preserved,
since separate bubbles arrive on separate lines) and strips the known
garbage classes. An empty return means "this panel has no usable text" —
the same meaning an empty OCR result always had. Every pattern here traces
to real output from a production run; extend the lists when new classes
appear rather than loosening the real-text path.
"""

from __future__ import annotations

import re

# Whole entry (or whole paragraph) = "there is no text here", in the model's
# various phrasings. Chinese placeholders arrive with/without parentheses and
# mix 图中/图片中, 无可辨识/没有可识别/无可提取, ...文字/文字内容.
_PLACEHOLDER_RE = re.compile(
    r"^[（(]?\s*(?:图中|图片中|此处)?[^（()）]{0,8}?"
    r"(?:无可?(?:辨识|识别|提取)?的?文字|没有(?:可识别的)?文字|无文字内容|有对话框)"
    r"[^（()）]{0,12}[)）]?[。.]?\s*$"
)

# Paragraph-level hallucination openers: scene descriptions and apologies the
# model produces instead of admitting "no text".
_HALLUCINATION_STARTERS = (
    "i am sorry",
    "i'm sorry",
    "the provided image",
    "the image is",
    "the image depicts",
    "this image",
    "这张图片",
    "以下是从图片中提取",
    "注释：",
)

_TABLE_RE = re.compile(r"<table>.*?(?:</table>|\Z)", re.IGNORECASE | re.DOTALL)
_LATEX_RE = re.compile(r"\\\[.*?\\\]", re.DOTALL)
# 12+ repeats of one character collapse to 3 ("AAAA…" walls, tildes, dashes).
_CHAR_RUN_RE = re.compile(r"(.)\1{11,}", re.DOTALL)

MAX_OCR_LENGTH = 600


def _is_garbage_paragraph(paragraph: str) -> bool:
    stripped = paragraph.strip()
    if not stripped:
        return True
    if _PLACEHOLDER_RE.match(stripped):
        return True
    lowered = stripped.lower()
    return any(lowered.startswith(prefix) for prefix in _HALLUCINATION_STARTERS)


def clean_ocr_text(text: str | None) -> str:
    """Real bubble/caption text only; "" when the panel has nothing usable."""
    if not text:
        return ""
    text = _TABLE_RE.sub(" ", text)
    text = _LATEX_RE.sub(" ", text)
    text = _CHAR_RUN_RE.sub(lambda m: m.group(1) * 3, text)

    kept = [p for p in text.split("\n") if not _is_garbage_paragraph(p)]
    cleaned = "\n".join(p.rstrip() for p in kept).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    if len(cleaned) > MAX_OCR_LENGTH:
        cleaned = cleaned[:MAX_OCR_LENGTH].rstrip() + " …[truncated]"
    return cleaned
