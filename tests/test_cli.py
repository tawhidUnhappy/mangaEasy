"""The single `mangaeasy` entry point: dispatch table integrity, help,
version, and unknown-command handling."""

import importlib.util

import pytest

import mangaeasy
from mangaeasy.cli import COMMANDS, main


def test_every_command_module_exists():
    """Each COMMANDS entry must point at an importable module — catches
    renamed/deleted modules whose dispatch line was forgotten."""
    missing = [name for name, (module_path, *_rest) in COMMANDS.items()
               if importlib.util.find_spec(module_path) is None]
    assert missing == []


def test_help_exits_zero(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "mangaeasy" in out
    assert "video" in out


def test_version_matches_package(capsys):
    assert main(["--version"]) == 0
    assert mangaeasy.__version__ in capsys.readouterr().out


def test_unknown_command_suggests_and_exits_2(capsys):
    assert main(["video-adio"]) == 2
    err = capsys.readouterr().err
    assert "unknown command" in err
    assert "video-audio" in err  # difflib suggestion


@pytest.mark.parametrize("flag", ["-h", "--help", "help"])
def test_help_aliases(flag, capsys):
    assert main([flag]) == 0
    assert "Usage" in capsys.readouterr().out
