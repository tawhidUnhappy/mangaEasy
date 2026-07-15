from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

from mangaeasy.path_safety import (
    UnsafePathComponentError,
    portable_prefix_template_arg,
    validate_portable_segment,
    validate_relative_subpath,
)
from mangaeasy.video_pipeline.common import project_name


def test_portable_segment_preserves_unicode_and_internal_spaces() -> None:
    assert validate_portable_segment("My Story 日本語") == "My Story 日本語"
    assert project_name(Path("unused"), "Mý Manga 第 1 部") == "Mý Manga 第 1 部"


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "..",
        "../escape",
        "..\\escape",
        "/absolute",
        "C:\\absolute",
        "C:drive-relative",
        "\\\\server\\share",
        "two/segments",
        "NUL",
        "con.txt",
        "trailing.",
        " leading",
        "bad:name",
    ],
)
def test_portable_segment_rejects_escape_and_nonportable_names(value: str) -> None:
    with pytest.raises(UnsafePathComponentError):
        validate_portable_segment(value)


def test_relative_subpath_allows_safe_nested_unicode_folders() -> None:
    assert validate_relative_subpath(r"raw pages\日本語") == "raw pages/日本語"


@pytest.mark.parametrize(
    "value",
    ["../escape", r"raw\..\escape", "/absolute", r"C:\absolute", "raw//pages", "raw/NUL"],
)
def test_relative_subpath_rejects_traversal_absolute_and_bad_segments(value: str) -> None:
    with pytest.raises(UnsafePathComponentError):
        validate_relative_subpath(value)


@pytest.mark.parametrize("value", ["{other}_", "{item.__class__}", "{item", "../{item}"])
def test_panel_prefix_rejects_unknown_format_fields_and_traversal(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        portable_prefix_template_arg(value)


def test_configured_media_subdirectories_cannot_escape_roots(tmp_path: Path, monkeypatch) -> None:
    import mangaeasy.paths as configured_paths
    from mangaeasy.library_scan import library_dir

    monkeypatch.setattr(configured_paths, "_path_cfg", lambda: {"library_subdir": "../escape"})
    with pytest.raises(UnsafePathComponentError):
        configured_paths.library_dir()

    (tmp_path / "config.system.json").write_text(
        '{"paths":{"library_subdir":"../escape"}}', encoding="utf-8"
    )
    with pytest.raises(UnsafePathComponentError):
        library_dir(tmp_path)


@pytest.mark.parametrize(
    ("command", "arguments"),
    [
        ("download", ["--name", "../escape"]),
        ("download", ["--chapter", "../escape"]),
        ("download", ["--chapters", "1", "../escape"]),
        ("style-detect", ["--source-subdir", "../escape"]),
        ("webtoon-split", ["--panels-subdir", r"C:\escape"]),
        ("webtoon-split", ["--prefix-template", "../../escape"]),
        ("webtoon-split", ["--prefix-template", "{item.__class__}"]),
        ("page-split", ["--source-subdir", "/escape"]),
        ("page-split", ["--prefix-template", r"..\escape"]),
        ("webtoon-cutcheck", ["--source-subdir", r"raw\..\escape"]),
        ("panels-remap", ["--old-run", "../escape"]),
        ("audio-takes-restore", ["--run", "../escape"]),
    ],
)
def test_direct_cli_folder_arguments_fail_cleanly(command: str, arguments: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mangaeasy.cli", command, *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 2
    assert "error:" in result.stderr.lower()
    assert "traceback" not in (result.stdout + result.stderr).lower()


def test_project_name_join_failure_is_a_clean_cli_error(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mangaeasy.cli",
            "audio-takes-list",
            "--project-root",
            str(tmp_path),
            "--audio-root",
            str(tmp_path / "audio"),
            "--project-name",
            "../escape",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 2
    assert "project name" in result.stderr.lower()
    assert "traceback" not in (result.stdout + result.stderr).lower()
