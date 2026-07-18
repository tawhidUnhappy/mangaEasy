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

Delivery stays natural at all times. IndexTTS2 renders scream/shout-intensity
emotion words as actual screaming or shouting far more often than not, and it
reads as broken audio rather than drama — so ``emotion`` must describe *tone*,
never shouted volume (see ``SCREAM_TERMS``/``emotion_lint``). The same
"describe, don't transcribe" rule applies to the narration text itself: a
phonetic laugh or scream ("ha ha ha", "gyahahaha", "aaaargh") is not a real
word, so TTS mangles it — write what happened instead ("she laughed",
"he let out a startled scream"). Real interjections and words the engine
actually pronounces correctly ("hmm", "huh", ellipses like "even though...")
are fine verbatim; see ``narration_delivery_lint``.

This module is deliberately import-light (no torch, no indextts): the QA
loop, narration-check, and tests all use it outside the TTS environment.
"""

from __future__ import annotations

import re

# Vocabulary that IndexTTS2's text-to-emotion encoder handles well. Free text
# beyond this list still works; this is guidance for prompt docs + QA linting,
# not a hard gate. Deliberately excludes scream/shout-intensity words (see
# SCREAM_TERMS) — even a panel-legit "terrified" character should be voiced
# with a natural, tense delivery, not an actual scream.
SUGGESTED_EMOTIONS = (
    "calm", "soft", "warm", "happy", "excited", "triumphant",
    "sad", "sorrowful", "tearful",
    "tense", "urgent", "fearful", "terrified",
    "angry", "furious", "cold", "menacing",
    "surprised", "shocked", "whispering", "solemn",
)

# Emotion words that push IndexTTS2 toward actually screaming/shouting the
# line instead of coloring a natural narrator voice — annoying and, most of
# the time, not even an accurate read of the panel. Blocked outright rather
# than merely discouraged: the fix is always a calmer synonym ("tense",
# "urgent", "fearful") that still conveys the moment.
SCREAM_TERMS = frozenset({
    "scream", "screaming", "screams", "screamed",
    "shout", "shouting", "shouts", "shouted",
    "yell", "yelling", "yells", "yelled",
    "shriek", "shrieking", "shrieks", "shrieked",
    "screech", "screeching", "screeches", "screeched",
    "bellow", "bellowing", "bellows", "bellowed",
    "howl", "howling", "howls", "howled",
})

# Narration text that spells out a laugh or scream phonetically instead of
# describing it in prose. TTS handles real words fine ("hmm", "even
# though...") but has no idea how to say "hahaha" or "gyaaaah" — it comes out
# garbled or, worse, an actual ear-splitting shout. Matches runs of 3+ laugh
# syllables (spaced, hyphenated, or run together) and elongated scream vowels
# ("aaaah", "aaaargh").
_LAUGH_SYLLABLE = r"(?:ha|ho|he|hi|fu|gya|kya|mu|bwa)"
LAUGH_SFX_PATTERN = re.compile(
    rf"\b(?:{_LAUGH_SYLLABLE}[\s,\-]*){{3,}}"
    r"|\b[aiu]{3,}h+\b"
    r"|\ba{2,}rgh*\b",
    re.IGNORECASE,
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
    stripped = value.strip()
    if len(stripped) > MAX_EMOTION_LENGTH:
        return f"emotion text longer than {MAX_EMOTION_LENGTH} chars — keep it a short phrase"
    words = {w.strip(".,!?").lower() for w in stripped.split()}
    hit = words & SCREAM_TERMS
    if hit:
        return (
            f"emotion {stripped!r} asks IndexTTS to {'/'.join(sorted(hit))} — it renders this as "
            "actual screaming/shouting most of the time, not natural delivery. Use a calmer synonym "
            "instead (e.g. 'tense', 'urgent', 'fearful', 'panicked') that still conveys the moment."
        )
    return None


def narration_delivery_lint(text: str) -> str | None:
    """A human-readable delivery problem with narration TEXT (not the emotion
    field), or None. Never blocks the pipeline — this is a review nudge, not
    a structural error, since a regex can't tell prose from an on-purpose SFX
    quote with full certainty."""
    if not isinstance(text, str) or not text.strip():
        return None
    match = LAUGH_SFX_PATTERN.search(text)
    if match:
        return (
            f"narration spells out a laugh/scream sound effect phonetically ({match.group(0)!r}) — "
            "TTS mispronounces or shouts these instead of speaking them naturally. Describe it in "
            "prose instead, e.g. 'she laughed' or 'he let out a startled scream'."
        )
    return None


def indextts_kwargs(emotion: str | None, emo_alpha: float = DEFAULT_EMO_ALPHA) -> dict:
    """Extra kwargs for IndexTTS2 ``infer()`` — empty dict when no emotion."""
    if not emotion:
        return {}
    return {"emo_text": emotion, "use_emo_text": True, "emo_alpha": emo_alpha}
