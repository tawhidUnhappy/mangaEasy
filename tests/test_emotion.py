"""Unit tests for the narration `emotion` field contract and delivery lints."""

from mediaconductor.audio.emotion import (
    emotion_lint,
    narration_delivery_lint,
    narration_emotion,
)


def test_narration_emotion_returns_stripped_value():
    assert narration_emotion({"emotion": "  cold, menacing  "}) == "cold, menacing"


def test_narration_emotion_none_when_absent_or_invalid():
    assert narration_emotion({}) is None
    assert narration_emotion({"emotion": ""}) is None
    assert narration_emotion({"emotion": 5}) is None
    assert narration_emotion({"emotion": "x" * 61}) is None


def test_emotion_lint_accepts_calm_vocabulary():
    assert emotion_lint({"emotion": "tense, urgent"}) is None
    assert emotion_lint({}) is None
    assert emotion_lint({"emotion": "  "}) is not None


def test_emotion_lint_rejects_scream_words():
    lint = emotion_lint({"emotion": "screaming, terrified"})
    assert lint is not None
    assert "scream" in lint.lower()


def test_emotion_lint_rejects_scream_words_in_a_sentence():
    lint = emotion_lint({"emotion": "she shouted angrily"})
    assert lint is not None
    assert "shout" in lint.lower()


def test_emotion_lint_flags_overlong_text():
    lint = emotion_lint({"emotion": "x" * 61})
    assert lint is not None
    assert "60" in lint


def test_narration_delivery_lint_flags_spaced_laugh():
    lint = narration_delivery_lint("Ha ha ha, you fool!")
    assert lint is not None


def test_narration_delivery_lint_flags_concatenated_laugh():
    lint = narration_delivery_lint("Hahaha, is that all you got?")
    assert lint is not None


def test_narration_delivery_lint_flags_elongated_scream():
    assert narration_delivery_lint("AAAAAH! Run!") is not None
    assert narration_delivery_lint("aaaargh, my arm!") is not None


def test_narration_delivery_lint_allows_real_words_and_interjections():
    assert narration_delivery_lint("Hmm, even though he tried, it failed.") is None
    assert narration_delivery_lint("He said hi to his friend.") is None
    assert narration_delivery_lint("She laughed nervously.") is None
    assert narration_delivery_lint("") is None
