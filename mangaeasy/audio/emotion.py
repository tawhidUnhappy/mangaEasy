"""Emotion-aware narration for TTS.

Narration entries may carry an optional ``"emotion"`` field next to
``"image"``/``"narration"``::

    {"image": "07_013_01.png",
     "narration": "Time Stop.",
     "emotion": "cold, menacing"}

IndexTTS2 accepts a natural-language emotion description (``emo_text``) and
blends it into the voice at a given strength (``emo_alpha``), so the field's
value is free text — but narration writers should stay inside the tested
vocabulary below; single words or short comma phrases steer best. Kokoro has
no emotion input; there the field is simply ignored, so narration stays
engine-portable.

This module is deliberately import-light (no torch, no indextts): the QA
loop, narration-check, and tests all use it outside the TTS environment.
"""

from __future__ import annotations

# Vocabulary that IndexTTS2's text-to-emotion encoder handles well. Free text
# beyond this list still works; this is guidance for prompt docs + QA linting,
# not a hard gate.
SUGGESTED_EMOTIONS = (
    "calm", "soft", "warm", "happy", "excited", "triumphant",
    "sad", "sorrowful", "tearful",
    "tense", "urgent", "fearful", "terrified",
    "angry", "furious", "cold", "menacing",
    "surprised", "shocked", "whispering", "solemn",
)

# Above this the cloned voice's identity starts to smear; below ~0.3 the
# effect is inaudible. 0.6 tracked well in listening checks.
DEFAULT_EMO_ALPHA = 0.6
MAX_EMOTION_LENGTH = 60


def narration_emotion(entry: dict) -> str | None:
    """The entry's usable emotion string, or None when absent/invalid.

    Invalid values (non-strings, empty, absurdly long) degrade to None
    instead of erroring: a bad emotion field must never break audio
    generation — QA (`work-qa`) reports it instead.
    """
    value = entry.get("emotion") if isinstance(entry, dict) else None
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > MAX_EMOTION_LENGTH:
        return None
    return value


def emotion_lint(entry: dict) -> str | None:
    """A human-readable problem with the entry's emotion field, or None."""
    if not isinstance(entry, dict) or "emotion" not in entry:
        return None
    value = entry["emotion"]
    if not isinstance(value, str) or not value.strip():
        return "emotion field must be a non-empty string"
    if len(value.strip()) > MAX_EMOTION_LENGTH:
        return f"emotion text longer than {MAX_EMOTION_LENGTH} chars — keep it a short phrase"
    return None


def indextts_kwargs(emotion: str | None, emo_alpha: float = DEFAULT_EMO_ALPHA) -> dict:
    """Extra kwargs for IndexTTS2 ``infer()`` — empty dict when no emotion."""
    if not emotion:
        return {}
    return {"emo_text": emotion, "use_emo_text": True, "emo_alpha": emo_alpha}
