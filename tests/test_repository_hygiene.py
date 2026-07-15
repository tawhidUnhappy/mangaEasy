"""Repository-context guards for generated, user-owned media projects."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "relative_path",
    [
        "projects/story-demo/images/scene-001.png",
        "projects/story-demo/render/final-story.mp4",
        "projects/song-demo/audio/generated-song.wav",
        "projects/song-demo/stems/vocals.flac",
    ],
)
def test_generated_projects_are_git_ignored(relative_path: str):
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", relative_path],
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0, f"expected Git to ignore {relative_path}"


def test_generated_projects_are_excluded_from_docker_context():
    docker_rules = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "/projects/" in docker_rules


def test_build_products_are_excluded_from_docker_context():
    docker_rules = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())
    assert {"/build/", "/dist/"} <= docker_rules


def test_sdist_allowlist_does_not_include_generated_projects():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"/projects"' not in pyproject
