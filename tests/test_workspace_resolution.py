"""Workspace-root resolution: commands run from the wrong cwd must still find
the registered workspace instead of silently creating a second library/ tree
(the D:\\library incident)."""

from __future__ import annotations

import json
from pathlib import Path

import mediaconductor.config as config


def _make_workspace(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text("{}", encoding="utf-8")
    return path


def test_env_var_wins(tmp_path: Path, monkeypatch):
    workspace = _make_workspace(tmp_path / "ws")
    monkeypatch.setenv("MEDIACONDUCTOR_PROJECT_ROOT", str(workspace))
    assert config._project_root() == workspace.resolve()


def test_cwd_with_config_json_wins_over_registration(tmp_path: Path, monkeypatch):
    cwd_workspace = _make_workspace(tmp_path / "cwd_ws")
    registered = _make_workspace(tmp_path / "registered")
    monkeypatch.delenv("MEDIACONDUCTOR_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("MEDIACONDUCTOR_HOME", str(tmp_path / "data"))
    assert config.register_workspace(registered) is not None
    monkeypatch.chdir(cwd_workspace)
    assert config._project_root() == cwd_workspace.resolve()


def test_registered_workspace_rescues_a_wrong_cwd(tmp_path: Path, monkeypatch):
    registered = _make_workspace(tmp_path / "registered")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.delenv("MEDIACONDUCTOR_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("MEDIACONDUCTOR_HOME", str(tmp_path / "data"))
    marker = config.register_workspace(registered)
    assert marker is not None and json.loads(marker.read_text(encoding="utf-8"))[
        "workspace_root"] == str(registered.resolve())
    monkeypatch.chdir(elsewhere)
    assert config._project_root() == registered.resolve()


def test_stale_registration_is_ignored(tmp_path: Path, monkeypatch):
    registered = _make_workspace(tmp_path / "registered")
    monkeypatch.delenv("MEDIACONDUCTOR_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("MEDIACONDUCTOR_HOME", str(tmp_path / "data"))
    assert config.register_workspace(registered) is not None
    (registered / "config.json").unlink()  # workspace was deleted/moved
    assert config._registered_workspace() is None


def test_register_workspace_refuses_non_workspaces(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MEDIACONDUCTOR_HOME", str(tmp_path / "data"))
    assert config.register_workspace(tmp_path / "no_config") is None
