"""Docs can't rot silently: every `mangaeasy <command>` mentioned in the
AI-facing docs must exist in the CLI's dispatch table, the repo's own
navigation docs must not contain broken internal links, and CLAUDE.md must
name every top-level package so a new package can't appear undocumented."""

import re
from pathlib import Path

from mangaeasy.cli import COMMANDS

REPO = Path(__file__).resolve().parents[1]

DOCS = [
    REPO / "docs" / "ai-guide.md",
    REPO / "docs" / "manga-video-guide.md",
    REPO / "docs" / "youtube.md",
    REPO / "docs" / "recap-video-playbook.md",
    REPO / "docs" / "operate" / "crop-verify-narrate.md",
    REPO / "docs" / "setup.md",
    REPO / "docs" / "multi-agent.md",
    REPO / "docs" / "thumbnail.md",
    REPO / "docs" / "install.md",
    REPO / "AGENTS.md",
    REPO / "CLAUDE.md",
    REPO / ".claude" / "skills" / "manga-recap" / "SKILL.md",
]

# Docs whose internal (relative) links are checked for existence. Scoped to
# the navigation set this reorg controls; widen as older docs are audited.
LINK_CHECKED_DOCS = [
    REPO / "CLAUDE.md",
    REPO / "docs" / "multi-agent.md",
    REPO / "AGENTS.md",
    REPO / "docs" / "operate" / "crop-verify-narrate.md",
    REPO / "docs" / "setup.md",
    REPO / "docs" / "history" / "reorg-plan.md",
    REPO / "docs" / "history" / "legacy-inventory.md",
    *sorted((REPO / "skills").rglob("*.md")),
]

PROFILE_AWARE_MANGA_DOCS = [
    REPO / ".claude" / "skills" / "manga-recap" / "SKILL.md",
    REPO / "docs" / "manga-video-guide.md",
    REPO / "docs" / "recap-video-playbook.md",
]

# markdown [text](target) where target is a relative path (not http/anchor)
LINK_RE = re.compile(r"\]\((?!https?://|#|mailto:)([^)]+)\)")

# `mediaconductor <token>` (or its legacy alias) where token looks like a subcommand (lowercase,
# digits, hyphens). Placeholders (`<command>`), flags (`--help`), version
# numbers, and prose ("mangaEasy CLI") don't match.
COMMAND_RE = re.compile(r"(?:mediaconductor|mangaeasy) ([a-z][a-z0-9-]*)")

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
    for needle in ("mediaconductor modes --json", "setup --mode", "doctor --mode",
                   "commands --mode", "MANGAEASY_RESULT", "MANGAEASY_PROGRESS",
                   "MANGAEASY_ROOT", "mediaconductor mcp --mode", "exit code"):
        assert needle.lower() in guide.lower(), f"ai-guide.md is missing: {needle}"


def test_active_manga_docs_require_profile_aware_youtube_auth():
    for doc in PROFILE_AWARE_MANGA_DOCS:
        text = doc.read_text(encoding="utf-8").lower()
        for needle in (
            "youtube-profiles --json",
            "youtube-status --profile",
            "youtube-upload --profile",
            "--no-auto-auth",
        ):
            assert needle in text, f"{doc.name} is missing profile-aware YouTube guidance: {needle}"
        assert "mangaeasy youtube-status" not in text
        assert "mangaeasy youtube-upload" not in text


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


def test_claude_md_names_every_top_level_package():
    """A new mangaeasy/<pkg>/ can't appear without being named in CLAUDE.md's code map."""
    claude_md = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    missing = [p.name for p in _packages() if p.name not in claude_md]
    assert not missing, f"CLAUDE.md does not mention packages: {missing}"


def test_every_package_has_a_readme():
    """Every mangaeasy/<pkg>/ must document itself with a README.md."""
    missing = [p.name for p in _packages() if not (p / "README.md").exists()]
    assert not missing, f"packages missing a README.md: {missing}"


def test_bundled_mode_skills_work_without_a_source_checkout():
    """Wheel/frozen agents must not be told that the repository is required."""
    for mode in ("manga-video", "ai-story", "song-video"):
        skill_dir = REPO / "skills" / mode
        skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        all_guidance = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(skill_dir.rglob("*.md"))
        )
        assert "<mc>" in skill
        assert "globally installed" in skill
        assert "frozen" in skill
        assert "uv --project D:/MediaConductor run mediaconductor" not in all_guidance

    router = (REPO / "skills" / "media-conductor" / "SKILL.md").read_text(encoding="utf-8")
    assert "Global/wheel install" in router
    assert "Frozen archive" in router
    assert "--allow-root <media-workspace>" in router


def test_packaged_youtube_reference_is_self_contained():
    text = (
        REPO / "skills" / "media-conductor" / "references" / "youtube-publishing.md"
    ).read_text(encoding="utf-8")
    for needle in (
        "Google Cloud Console",
        "Desktop app",
        "shared_client_file",
        "youtube-auth --profile",
        "browser opens automatically",
    ):
        assert needle.lower() in text.lower()
