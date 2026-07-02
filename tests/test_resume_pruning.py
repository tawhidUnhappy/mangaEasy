"""Shard-aware resume pruning: with --gpu-workers > 1 each shard has its own
in-progress boundary, and pruning must respect it (see CLAUDE.md)."""

from mangaeasy.utils import LazyArchiveRunDir
from mangaeasy.video_pipeline.common import prune_recent_audio_for_resume


def make_paths(tmp_path, count, existing):
    item_dir = tmp_path / "01"
    item_dir.mkdir(exist_ok=True)
    paths = [item_dir / f"panel_{i:03d}.wav" for i in range(count)]
    for i in existing:
        paths[i].write_bytes(b"x")
    return paths


def test_prunes_current_plus_lookback(tmp_path):
    # Files 0..5 exist, 6 is the in-progress boundary; lookback 2 archives 4,5.
    paths = make_paths(tmp_path, 10, existing=range(6))
    lazy = LazyArchiveRunDir(tmp_path / "old")
    removed = prune_recent_audio_for_resume(paths, lazy, lookback=2, shards=1)
    assert removed == [paths[4], paths[5]]
    assert not paths[4].exists() and not paths[5].exists()
    assert paths[3].exists()


def test_all_files_present_prunes_from_end(tmp_path):
    paths = make_paths(tmp_path, 5, existing=range(5))
    lazy = LazyArchiveRunDir(tmp_path / "old")
    removed = prune_recent_audio_for_resume(paths, lazy, lookback=1, shards=1)
    assert removed == [paths[3], paths[4]]


def test_sharded_prune_checks_each_shard_boundary(tmp_path):
    # Two workers, 10 files → shards [0..4] and [5..9]. Worker 1 stopped at
    # index 2, worker 2 at index 7 (i.e. 0,1 and 5,6 exist).
    paths = make_paths(tmp_path, 10, existing=[0, 1, 5, 6])
    lazy = LazyArchiveRunDir(tmp_path / "old")
    removed = prune_recent_audio_for_resume(paths, lazy, lookback=5, shards=2)
    # Both shards' recent files pruned — not just the first shard's.
    assert set(removed) == {paths[0], paths[1], paths[5], paths[6]}


def test_nothing_to_prune_allocates_no_run_dir(tmp_path):
    paths = make_paths(tmp_path, 4, existing=[])
    lazy = LazyArchiveRunDir(tmp_path / "old")
    removed = prune_recent_audio_for_resume(paths, lazy, lookback=3, shards=1)
    assert removed == []
    assert lazy.allocated is None
    assert not (tmp_path / "old").exists()
