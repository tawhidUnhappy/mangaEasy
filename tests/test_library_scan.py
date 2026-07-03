"""`library-list` state discovery: both on-disk layouts, read-only."""

import json

from mangaeasy.library_scan import scan_library


def make_project(root, layout: str):
    lib = root / "library"
    proj = lib / "myproj"
    item = proj / "01"
    panels = item / "panels"
    panels.mkdir(parents=True)
    (panels / "p001.png").write_bytes(b"x")
    (panels / "p002.png").write_bytes(b"x")
    (panels / "notes.txt").write_bytes(b"x")  # non-image, must not count
    entries = [{"image": "p001.png", "narration": "a"}, {"image": "p002.png", "narration": "b"}]
    if layout == "item":
        (item / "narration.json").write_text(json.dumps(entries), encoding="utf-8")
        (item / "intro.json").write_text(json.dumps(entries[:1]), encoding="utf-8")
    else:
        (item / "narration_01.json").write_text(json.dumps(entries), encoding="utf-8")
        (item / "audio").mkdir()
        (item / "audio" / "p001.wav").write_bytes(b"x")
        (item / "01_myproj.mp4").write_bytes(b"x")
    return root


def test_item_pipeline_layout(tmp_path):
    make_project(tmp_path, "item")
    report = scan_library(tmp_path)
    assert len(report["projects"]) == 1
    item = report["projects"][0]["items"][0]
    assert item["item"] == "01"
    assert item["panels"] == 2
    assert item["narration_file"] == "narration.json"
    assert item["narration_entries"] == 2
    assert item["has_intro"] is True
    assert item["local_audio"] == 0


def test_legacy_chapter_layout(tmp_path):
    make_project(tmp_path, "legacy")
    item = scan_library(tmp_path)["projects"][0]["items"][0]
    assert item["narration_file"] == "narration_01.json"
    assert item["local_audio"] == 1
    assert item["local_videos"] == ["01_myproj.mp4"]


def test_configured_library_subdir(tmp_path):
    make_project(tmp_path, "item")
    (tmp_path / "library").rename(tmp_path / "content")
    (tmp_path / "config.system.json").write_text(
        json.dumps({"paths": {"library_subdir": "content"}}), encoding="utf-8"
    )
    report = scan_library(tmp_path)
    assert report["library"].endswith("content")
    assert len(report["projects"]) == 1


def test_empty_root_is_not_an_error(tmp_path):
    report = scan_library(tmp_path)
    assert report["projects"] == []
