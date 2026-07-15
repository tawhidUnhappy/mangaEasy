from __future__ import annotations

import subprocess
import sys

import pytest

from mangaeasy.workboard import _claim_file


def test_claim_components_cannot_escape_project(tmp_path):
    with pytest.raises(ValueError):
        _claim_file(tmp_path, item="../outside", stage="render", resource=None)
    with pytest.raises(ValueError):
        _claim_file(tmp_path, item=None, stage=None, resource="../../gpu")


def test_narration_item_cannot_escape_project(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "mangaeasy.cli", "narration-edit",
         "--project-root", str(tmp_path), "--item", "../outside", "--list"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 1
    assert "direct-child" in proc.stdout


def test_cleanup_all_requires_root_and_exact_confirmation(tmp_path):
    project = tmp_path / "project"
    target = tmp_path / "generated" / "project"
    project.mkdir()
    target.mkdir(parents=True)
    (target / "video.mp4").write_bytes(b"x")
    base = [sys.executable, "-m", "mangaeasy.cli", "video-clean-all",
            "--project-root", str(project), "--dir", str(target), "--yes"]
    refused = subprocess.run(base, capture_output=True, text=True, encoding="utf-8")
    assert refused.returncode != 0 and target.exists()
    wrong = subprocess.run(
        [*base, "--allowed-root", str(tmp_path / "generated"), "--confirm-name", "wrong"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert wrong.returncode != 0 and target.exists()
    allowed = subprocess.run(
        [*base, "--allowed-root", str(tmp_path / "generated"), "--confirm-name", "project"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert allowed.returncode == 0 and not target.exists()
