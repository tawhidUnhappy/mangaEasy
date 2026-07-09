"""Docs can't rot silently: every `mangaeasy <command>` mentioned in the
AI-facing docs must exist in the CLI's dispatch table, the repo's own
navigation docs must not contain broken internal links, and START_HERE must
name every top-level package so a new package can't appear undocumented."""

import re
from pathlib import Path

from mangaeasy.cli import COMMANDS

REPO = Path(__file__).resolve().parents[1]

DOCS = [
    REPO / "docs" / "ai-guide.md",
    REPO / "docs" / "youtube.md",
    REPO / "docs" / "recap-video-playbook.md",
    REPO / "docs" / "operate" / "crop-verify-narrate.md",
    REPO / "docs" / "setup.md",
    REPO / "docs" / "thumbnail.md",
    REPO / "docs" / "install.md",
    REPO / "START_HERE.md",
    REPO / "AGENTS.md",
    REPO / ".claude" / "skills" / "manga-recap" / "SKILL.md",
]

# Docs whose internal (relative) links are checked for existence. Scoped to
# the navigation set this reorg controls; widen as older docs are audited.
LINK_CHECKED_DOCS = [
    REPO / "START_HERE.md",
    REPO / "AGENTS.md",
    REPO / "docs" / "operate" / "crop-verify-narrate.md",
    REPO / "docs" / "setup.md",
    REPO / "docs" / "history" / "reorg-plan.md",
    REPO / "docs" / "history" / "legacy-inventory.md",
]

# markdown [text](target) where target is a relative path (not http/anchor)
LINK_RE = re.compile(r"\]\((?!https?://|#|mailto:)([^)]+)\)")

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


def test_internal_doc_links_resolve():
    """Relative links in the navigation docs must point at real files/dirs."""
    broken: dict[str, set[str]] = {}
    for doc in LINK_CHECKED_DOCS:
        text = doc.read_text(encoding="utf-8")
        for target in LINK_RE.findall(text):
            path_part = target.split("#", 1)[0]  # drop #Lnn / #anchor fragments
            if not path_part:
                continue
            resolved = (doc.parent / path_part).resolve()
            if not resolved.exists():
                broken.setdefault(doc.name, set()).add(target)
    assert not broken, f"docs contain broken internal links: {broken}"


def _packages() -> list:
    pkg_root = REPO / "mangaeasy"
    return sorted(
        p for p in pkg_root.iterdir()
        if p.is_dir() and (p / "__init__.py").exists() and not p.name.startswith("_")
    )


def test_start_here_names_every_top_level_package():
    """A new mangaeasy/<pkg>/ can't appear without being named in START_HERE."""
    start_here = (REPO / "START_HERE.md").read_text(encoding="utf-8")
    missing = [p.name for p in _packages() if p.name not in start_here]
    assert not missing, f"START_HERE.md does not mention packages: {missing}"


def test_every_package_has_a_readme():
    """Every mangaeasy/<pkg>/ must document itself with a README.md."""
    missing = [p.name for p in _packages() if not (p / "README.md").exists()]
    assert not missing, f"packages missing a README.md: {missing}"
