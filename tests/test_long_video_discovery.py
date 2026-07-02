"""find_latest_long_video picks the newest plain join, never a BGM mix or
an archived file — plus the version-sync invariant the release relies on."""

import os
import tomllib
from pathlib import Path

import mangaeasy
from mangaeasy.video_pipeline.common import find_latest_long_video


def test_picks_newest_plain_join(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    older = project / "myproj_full_20250101.mp4"
    newer = project / "myproj_full_20250201.mp4"
    older.write_bytes(b"a")
    newer.write_bytes(b"b")
    os.utime(older, (1_000_000, 1_000_000))
    os.utime(newer, (2_000_000, 2_000_000))
    assert find_latest_long_video(tmp_path, "myproj") == newer


def test_skips_bgm_mixes_and_archived(tmp_path):
    project = tmp_path / "myproj"
    project.mkdir()
    plain = project / "myproj_full_20250101.mp4"
    plain.write_bytes(b"a")
    bgm = project / "myproj_full_20250101_bgm_20250301.mp4"
    bgm.write_bytes(b"b")
    os.utime(plain, (1_000_000, 1_000_000))
    os.utime(bgm, (2_000_000, 2_000_000))
    old_dir = project / "old" / "run_0001"
    old_dir.mkdir(parents=True)
    (old_dir / "myproj_full_archived.mp4").write_bytes(b"c")
    assert find_latest_long_video(tmp_path, "myproj") == plain


def test_missing_project_dir_returns_none(tmp_path):
    assert find_latest_long_video(tmp_path, "nope") is None


def test_version_is_in_sync_with_pyproject():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as f:
        version = tomllib.load(f)["project"]["version"]
    assert mangaeasy.__version__ == version
