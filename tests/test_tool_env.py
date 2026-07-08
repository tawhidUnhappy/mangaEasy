"""Cache-isolation contract for tool subprocess environments.

The self-contained promise ("everything mangaEasy writes lives in one
folder") only holds if a tool subprocess's HF/torch/uv caches land under
`.mangaeasy/`, even on a machine that exports a global `HF_HOME`/
`UV_CACHE_DIR` for other tools. `tool_env()` force-pins them for exactly
that reason; `MANGAEASY_SHARE_CACHES=1` is the opt-out.
"""

import os

from mangaeasy.tools.external import tool_env

CACHE_VARS = ("HF_HOME", "HF_HUB_CACHE", "TRANSFORMERS_CACHE", "TORCH_HOME", "UV_CACHE_DIR")


def test_forces_caches_under_mangaeasy_over_inherited_globals(tmp_path, monkeypatch):
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path / ".mangaeasy"))
    monkeypatch.delenv("MANGAEASY_SHARE_CACHES", raising=False)
    # A hostile ambient environment pointing caches elsewhere.
    for var in CACHE_VARS:
        monkeypatch.setenv(var, r"D:\somewhere\else")

    env = tool_env()
    home = str(tmp_path / ".mangaeasy")
    for var in CACHE_VARS:
        assert env[var].startswith(home), f"{var}={env[var]} escaped {home}"


def test_share_caches_opt_in_defers_to_inherited(tmp_path, monkeypatch):
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path / ".mangaeasy"))
    monkeypatch.setenv("MANGAEASY_SHARE_CACHES", "1")
    monkeypatch.setenv("HF_HOME", r"D:\shared\hf")

    env = tool_env()
    assert env["HF_HOME"] == r"D:\shared\hf"
    # Vars the ambient env does NOT set still fall back under .mangaeasy.
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    assert str(tmp_path / ".mangaeasy") in tool_env()["UV_CACHE_DIR"]


def test_drops_virtualenv_and_pythonhome(tmp_path, monkeypatch):
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path / ".mangaeasy"))
    monkeypatch.setenv("VIRTUAL_ENV", r"C:\some\venv")
    monkeypatch.setenv("PYTHONHOME", r"C:\python")
    env = tool_env()
    assert "VIRTUAL_ENV" not in env
    assert "PYTHONHOME" not in env


def test_share_caches_accepts_truthy_spellings(tmp_path, monkeypatch):
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path / ".mangaeasy"))
    monkeypatch.setenv("HF_HOME", r"D:\shared\hf")
    for val in ("1", "true", "YES", "On"):
        monkeypatch.setenv("MANGAEASY_SHARE_CACHES", val)
        assert tool_env()["HF_HOME"] == r"D:\shared\hf", val
    monkeypatch.setenv("MANGAEASY_SHARE_CACHES", "0")
    assert os.sep in tool_env()["HF_HOME"] and "shared" not in tool_env()["HF_HOME"]
