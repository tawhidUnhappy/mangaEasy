"""Docs can't rot silently: every `mangaeasy <command>` mentioned in the
AI-facing docs must exist in the CLI's dispatch table."""

import re
from pathlib import Path

from mangaeasy.cli import COMMANDS

DOCS = [
    Path(__file__).resolve().parents[1] / "docs" / "ai-guide.md",
    Path(__file__).resolve().parents[1] / "AGENTS.md",
]

# `mangaeasy <token>` where token looks like a subcommand (lowercase,
# digits, hyphens). Placeholders (`<command>`), flags (`--help`), version
# numbers, and prose ("mangaEasy CLI") don't match.
COMMAND_RE = re.compile(r"mangaeasy ([a-z][a-z0-9-]*)")

KNOWN_NON_COMMANDS = {"help", "version"}  # CLI built-ins


def test_docs_reference_only_real_commands():
    unknown: dict[str, set[str]] = {}
    for doc in DOCS:
        text = doc.read_text(encoding="utf-8")
        for token in COMMAND_RE.findall(text):
            if token not in COMMANDS and token not in KNOWN_NON_COMMANDS:
                unknown.setdefault(doc.name, set()).add(token)
    assert not unknown, f"docs mention nonexistent commands: {unknown}"


def test_docs_cover_the_agent_essentials():
    guide = DOCS[0].read_text(encoding="utf-8")
    for needle in ("mangaeasy where --json", "mangaeasy commands --json",
                   "MANGAEASY_RESULT", "MANGAEASY_PROGRESS", "MANGAEASY_ROOT",
                   "mangaeasy mcp", "exit code", "library-list"):
        assert needle.lower() in guide.lower(), f"ai-guide.md is missing: {needle}"
