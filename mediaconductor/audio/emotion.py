"""Calm, emotion-aware narration for TTS.

Narration entries may carry an optional ``"emotion"`` field next to
``"image"``/``"narration"``::

    {"image": "07_013_01.png",
     "narration": "He quietly stops time.",
     "emotion": "calm"}

IndexTTS2 accepts a natural-language emotion description (``emo_text``) and
blends it into the voice at a given strength (``emo_alpha``). MediaConductor's
narrator is deliberately restrained: the only accepted hints are ``calm``,
``neutral``, ``slightly sad``, and ``slightly happy``. An absent field means
neutral. Invalid or high-intensity hints are ignored by TTS and reported by QA,
so skipping QA cannot accidentally produce a screamed performance. Kokoro has
no emotion input; there the field is simply ignored.

The same "describe, don't perform" rule applies to narration text. Phonetic
laughs and vocal noises ("ghaha", "ha ha ha", "aaaargh") are not prose and
can make TTS shout or garble the line. State the event calmly instead ("he
laughed", "she reacted in pain"). Exclamation marks, repeated question marks,
and shout-like all-caps phrasing are also rejected by the delivery lint. Real
words and quiet interjections ("hmm", "huh", ellipses like "even though...")
remain valid; see ``narration_delivery_lint``.

This module is deliberately import-light (no torch, no indextts): the QA
loop, TTS/render preflight, and tests all use it outside the TTS environment.
"""

from __future__ import annotations

import re

# This is the complete emotion vocabulary, not merely a suggestion. Keeping it
# tiny makes the narrator consistent across chapters and prevents a
# model-authored hint such as "furious" or "panicked" from becoming a loud
# performance.
SUGGESTED_EMOTIONS = (
    "calm",
    "neutral",
    "slightly sad",
    "slightly happy",
)
ALLOWED_EMOTIONS = frozenset(SUGGESTED_EMOTIONS)

# Kept separately so QA can give a precise explanation for the most dangerous
# legacy hints. All values outside ALLOWED_EMOTIONS are rejected, including
# less explicit high-intensity words such as "furious", "panicked", or
# "terrified".
SCREAM_TERMS = frozenset({
    "scream", "screaming", "screams", "screamed",
    "shout", "shouting", "shouts", "shouted",
    "yell", "yelling", "yells", "yelled",
    "shriek", "shrieking", "shrieks", "shrieked",
    "screech", "screeching", "screeches", "screeched",
    "bellow", "bellowing", "bellows", "bellowed",
    "howl", "howling", "howls", "howled",
})

# Narration text that spells out a laugh or another vocal sound instead of
# describing it in prose. The joined form includes the real-world failure that
# motivated this rule ("ghaha") as well as common variants. The spaced branch
# uses a back-reference so ordinary neighboring syllables do not match.
_JOINED_LAUGH = r"(?:(?:gya|kya|bwa|mwa|mua|mu|fu|ga|g)?(?:ha){2,}|(?:he|hi|ho|fu){2,})"
_SPACED_LAUGH = (
    r"(?:(?:g|gy|ky|bwa|mwa|mua|mu)?(?P<laugh>ha|he|hi|ho|fu))"
    r"(?:[\s,\-]+(?P=laugh)){1,}"
)
VOCAL_SFX_PATTERN = re.compile(
    rf"\b(?:{_JOINED_LAUGH}|{_SPACED_LAUGH}"
    r"|(?:gy|ky|gr|w)?(?P<scream_vowel>[aeiou])(?P=scream_vowel){2,}(?:h+|r+g+h*)?"
    r"|a+r+g+h+|u+g+h+|g+r{2,})\b",
    re.IGNORECASE,
)
# Backward-compatible public name used by older integrations.
LAUGH_SFX_PATTERN = VOCAL_SFX_PATTERN

# An exclamation mark directly asks most TTS engines for a more forceful
# delivery. Three or more all-caps words serve the same purpose. A single
# all-caps token is blocked only when it is a common shouted command, so real
# acronyms such as NASA, HTML, MMORPG, MC, and NPC remain valid.
_ALL_CAPS_RUN_PATTERN = re.compile(
    r"(?:\b[A-Z]{2,}\b(?:\s+|[,.]\s*)){2,}\b[A-Z]{2,}\b"
)
_ALL_CAPS_ACRONYMS = frozenset({
    "AI", "API", "DNA", "EU", "HQ", "HTML", "MC", "MMORPG", "MP",
    "NASA", "NATO", "NPC", "RPG", "UK", "UN", "US", "VR", "XP",
})
_SHOUT_CAPS_PATTERN = re.compile(
    r"\b(?:STOP|HELP|RUN|DIE|KILL|ATTACK|ESCAPE|WAIT|NEVER|NOW|LEAVE|SILENCE"
    r"|NO|GO|YES|FIRE|ENOUGH)\b"
)
_ELONGATED_VOWEL_PATTERN = re.compile(
    r"\b[A-Za-z]*(?P<vowel>[aeiou])(?P=vowel){2,}[A-Za-z]*\b",
    re.IGNORECASE,
)
_REPEATED_QUESTION_PATTERN = re.compile(r"\?{2,}")

# Above this the cloned voice's identity starts to smear; below about 0.3 the
# allowed slight emotion is difficult to hear. A caller can still use
# ``--no-emotion`` for completely neutral delivery.
DEFAULT_EMO_ALPHA = 0.6
MAX_EMOTION_LENGTH = 60


def _canonical_emotion(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    canonical = " ".join(value.strip().lower().split())
    if canonical not in ALLOWED_EMOTIONS:
        return None
    return canonical


def narration_emotion(entry: dict) -> str | None:
    """Return the entry's safe, canonical emotion, or ``None``.

    Invalid, empty, overlong, and high-intensity values degrade to neutral
    rather than reaching TTS. ``work-qa`` reports the invalid field so authors
    can remove or replace it.
    """
    value = entry.get("emotion") if isinstance(entry, dict) else None
    if isinstance(value, str) and len(value.strip()) > MAX_EMOTION_LENGTH:
        return None
    return _canonical_emotion(value)


def emotion_lint(entry: dict) -> str | None:
    """Return a human-readable problem with an emotion field, or ``None``."""
    if not isinstance(entry, dict) or "emotion" not in entry:
        return None
    value = entry["emotion"]
    if not isinstance(value, str) or not value.strip():
        return "emotion field must be a non-empty string"
    stripped = value.strip()
    if len(stripped) > MAX_EMOTION_LENGTH:
        return f"emotion text longer than {MAX_EMOTION_LENGTH} chars - keep it a short phrase"
    canonical = " ".join(stripped.lower().split())
    if canonical in ALLOWED_EMOTIONS:
        return None
    words = {word.strip(".,!?").lower() for word in stripped.split()}
    hit = words & SCREAM_TERMS
    if hit:
        return (
            f"emotion {stripped!r} asks IndexTTS to {'/'.join(sorted(hit))}; it renders this as "
            "actual screaming or shouting, not calm narration. Drop the field for neutral delivery "
            "or use exactly 'calm', 'slightly sad', or 'slightly happy'."
        )
    return (
        f"emotion {stripped!r} violates the calm-narration policy. Drop the field for neutral delivery "
        "or use exactly 'calm', 'neutral', 'slightly sad', or 'slightly happy'; high-intensity hints "
        "such as tense, panicked, angry, excited, or terrified are not allowed."
    )


def narration_delivery_lint(text: str) -> str | None:
    """Return a calm-delivery problem with narration text, or ``None``.

    ``work-qa`` treats this as an error because unsafe text can create loud
    audio even when no emotion field is present.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    match = VOCAL_SFX_PATTERN.search(text)
    if match:
        return (
            f"narration performs a laugh or vocal sound phonetically ({match.group(0)!r}); "
            "TTS can garble or shout it. Describe the event in calm prose instead, e.g. "
            "'he laughed' or 'she reacted in pain'."
        )
    if "!" in text:
        return (
            "narration contains an exclamation mark, which can trigger a loud or excited TTS delivery. "
            "Rewrite it as a calm statement ending with a period."
        )
    repeated_question = _REPEATED_QUESTION_PATTERN.search(text)
    if repeated_question:
        return (
            "narration contains repeated question marks, which can trigger an exaggerated TTS delivery. "
            "Rewrite it as a calm statement or use one question mark."
        )
    elongated = _ELONGATED_VOWEL_PATTERN.search(text)
    if elongated:
        return (
            f"narration elongates a word for vocal performance ({elongated.group(0)!r}). "
            "Rewrite it as normal calm prose."
        )
    caps = _SHOUT_CAPS_PATTERN.search(text)
    caps_run = _ALL_CAPS_RUN_PATTERN.search(text)
    if caps is None and caps_run is not None:
        words = set(re.findall(r"\b[A-Z]{2,}\b", caps_run.group(0)))
        if not words.issubset(_ALL_CAPS_ACRONYMS):
            caps = caps_run
    if caps:
        return (
            f"narration uses shout-like all-caps text ({caps.group(0)!r}). Rewrite it as normal-case, "
            "calm descriptive prose."
        )
    return None


def indextts_kwargs(emotion: str | None, emo_alpha: float = DEFAULT_EMO_ALPHA) -> dict:
    """Return safe extra kwargs for IndexTTS2, or an empty dict for neutral."""
    emotion = _canonical_emotion(emotion)
    if emotion is None:
        return {}
    return {"emo_text": emotion, "use_emo_text": True, "emo_alpha": emo_alpha}
