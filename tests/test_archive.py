"""Archive-before-overwrite: generated output is moved into old/run_NNNN/,
never silently destroyed."""

from mangaeasy.utils import LazyArchiveRunDir, archive_before_overwrite, archive_into_run, next_archive_run_dir


def test_archive_before_overwrite_moves_into_run_dir(tmp_path):
    out = tmp_path / "video.mp4"
    out.write_bytes(b"old take")
    archived = archive_before_overwrite(out)
    assert archived is not None
    assert not out.exists()
    assert archived.read_bytes() == b"old take"
    assert archived.parent.parent == tmp_path / "old"
    assert archived.parent.name == "run_0001"


def test_archive_before_overwrite_missing_file_is_noop(tmp_path):
    assert archive_before_overwrite(tmp_path / "nothing.mp4") is None
    assert not (tmp_path / "old").exists()


def test_run_numbers_increment(tmp_path):
    first = next_archive_run_dir(tmp_path / "old")
    second = next_archive_run_dir(tmp_path / "old")
    assert first.name == "run_0001"
    assert second.name == "run_0002"


def test_lazy_run_dir_allocates_only_on_use(tmp_path):
    lazy = LazyArchiveRunDir(tmp_path / "old")
    assert lazy.allocated is None
    assert not (tmp_path / "old").exists()
    run_dir = lazy.dir
    assert lazy.allocated == run_dir
    assert run_dir.is_dir()
    # Reused, not re-allocated
    assert lazy.dir == run_dir


def test_archive_into_run_with_subdir(tmp_path):
    src = tmp_path / "01" / "panel.wav"
    src.parent.mkdir()
    src.write_bytes(b"audio")
    run_dir = tmp_path / "old" / "run_0001"
    dest = archive_into_run(src, run_dir, subdir="01")
    assert dest == run_dir / "01" / "panel.wav"
    assert dest.read_bytes() == b"audio"
    assert not src.exists()
