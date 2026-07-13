"""Pure-logic tests for the agent-flow layer: setup planning, full-series
download helpers, style detection verdicts, narration structural checks, and
the fixed-window series batcher."""

import json

import pytest
from PIL import Image

from mangaeasy.download.mangadex import _chapter_sort_key, _slugify_project_name
from mangaeasy.panels.style_detect import measure_item, verdict_from_stats
from mangaeasy.series_plan import build_plan, load_publish_json, save_publish_json
from mangaeasy.tools.setup import BASE_TOOLS, GPU_TOOLS, plan_tools
from mangaeasy.video_pipeline.narration_check import check_item


# ── setup planning ──────────────────────────────────────────────────────────

def test_plan_tools_auto_without_gpu_is_base_only():
    assert plan_tools("auto", gpu=False, skip=set()) == BASE_TOOLS


def test_plan_tools_auto_with_gpu_adds_gpu_tools():
    assert plan_tools("auto", gpu=True, skip=set()) == BASE_TOOLS + GPU_TOOLS


def test_plan_tools_minimal_and_skip():
    assert plan_tools("minimal", gpu=True, skip=set()) == []
    assert "z-image-turbo" not in plan_tools("auto", gpu=True, skip={"z-image-turbo"})


# ── download helpers ────────────────────────────────────────────────────────

def test_chapter_sort_key_orders_decimals_and_specials():
    chapters = ["10", "2", "1.5", "Extra", "1"]
    assert sorted(chapters, key=_chapter_sort_key) == ["1", "1.5", "2", "10", "Extra"]


def test_slugify_project_name_is_filesystem_safe():
    assert _slugify_project_name("Omniscient Reader's Viewpoint!") == \
        "Omniscient_Reader_s_Viewpoint"
    assert _slugify_project_name("???") == "manga"


# ── style detection ─────────────────────────────────────────────────────────

def _make_images(folder, sizes):
    folder.mkdir(parents=True)
    for i, (w, h) in enumerate(sizes):
        Image.new("RGB", (w, h)).save(folder / f"p{i:02d}.png")


def test_style_detect_webtoon_and_paged(tmp_path):
    _make_images(tmp_path / "wt", [(800, 8000)] * 4)
    _make_images(tmp_path / "pg", [(1080, 1600)] * 4)
    assert verdict_from_stats(measure_item(tmp_path / "wt")) == "webtoon"
    assert verdict_from_stats(measure_item(tmp_path / "pg")) == "paged"


def test_style_detect_empty_dir_returns_none(tmp_path):
    (tmp_path / "empty").mkdir()
    assert measure_item(tmp_path / "empty") is None


# ── narration structural checks ─────────────────────────────────────────────

@pytest.fixture
def item(tmp_path):
    item_dir = tmp_path / "01"
    panels = item_dir / "panels"
    panels.mkdir(parents=True)
    for name in ("a.jpg", "b.jpg"):
        (panels / name).write_bytes(b"x")
    return item_dir


def test_narration_check_clean(item):
    (item / "narration.json").write_text(json.dumps([
        {"image": "a.jpg", "narration": "one"},
        {"image": "b.jpg", "narration": "two"},
    ]))
    assert check_item(item)["ok"]


def test_narration_check_flags_all_defect_classes(item):
    (item / "narration.json").write_text(json.dumps([
        {"image": "a.jpg", "narration": "  "},       # empty text
        {"image": "ghost.jpg", "narration": "hi"},   # dangling image
    ]))                                              # b.jpg uncovered
    report = check_item(item)
    assert not report["ok"]
    # Uncovered panels are a WARNING, not a problem: skipping credits/banner
    # panels is the documented correct workflow, and treating them as errors
    # used to fail every correctly-produced project.
    assert report["uncovered_panels"] == ["b.jpg"]
    assert len(report["warnings"]) == 1
    assert len(report["problems"]) == 2


def test_narration_check_uncovered_alone_still_passes(item):
    (item / "narration.json").write_text(json.dumps([
        {"image": "a.jpg", "narration": "one"},
    ]))                                              # b.jpg uncovered only
    report = check_item(item)
    assert report["ok"]
    assert report["uncovered_panels"] == ["b.jpg"]
    assert report["warnings"] and not report["problems"]


def test_narration_check_intro_json_is_covered_separately(item):
    (item / "narration.json").write_text(json.dumps([
        {"image": "a.jpg", "narration": "one"},
    ]))
    (item / "intro.json").write_text(json.dumps([
        {"image": "b.jpg", "narration": "hook"},
    ]))
    assert check_item(item)["ok"]  # intro entries count toward coverage


def test_narration_check_flags_intro_narration_overlap(item):
    # A cold-open panel that also appears in narration.json plays twice —
    # the intro is prepended, then the same panel shows again in-context.
    (item / "narration.json").write_text(json.dumps([
        {"image": "a.jpg", "narration": "one"},
        {"image": "b.jpg", "narration": "two"},
    ]))
    (item / "intro.json").write_text(json.dumps([
        {"image": "a.jpg", "narration": "cold open"},
    ]))
    report = check_item(item)
    assert not report["ok"]
    assert any("both intro.json and narration.json" in p for p in report["problems"])


# ── series batching ─────────────────────────────────────────────────────────

def _make_project(tmp_path, items, narrated):
    root = tmp_path / "proj"
    for name in items:
        panels = root / name / "panels"
        panels.mkdir(parents=True)
        (panels / "p.jpg").write_bytes(b"x")
        if name in narrated:
            (root / name / "narration.json").write_text(
                json.dumps([{"image": "p.jpg", "narration": "hi"}]))
    return root


def test_series_plan_windows_are_stable_and_partial_flagged(tmp_path):
    items = [f"{i:02d}" for i in range(1, 27)]  # 26 items
    root = _make_project(tmp_path, items, narrated=set(items))
    plan = build_plan(root, batch_size=12)
    assert [b["batch"] for b in plan["batches"]] == ["01-12", "13-24", "25-26"]
    assert [b["full"] for b in plan["batches"]] == [True, True, False]
    assert plan["next_batch"]["batch"] == "01-12"


def test_series_plan_advances_past_published(tmp_path):
    items = [f"{i:02d}" for i in range(1, 25)]
    root = _make_project(tmp_path, items, narrated=set(items))
    publish = load_publish_json(root)
    publish["published"].append({"items": items[:12], "video_id": "vid1"})
    save_publish_json(root, publish)
    plan = build_plan(root, batch_size=12)
    assert plan["batches"][0]["published"] and plan["batches"][0]["video_id"] == "vid1"
    assert plan["next_batch"]["batch"] == "13-24"


def test_series_plan_readiness_requires_narration(tmp_path):
    items = [f"{i:02d}" for i in range(1, 13)]
    root = _make_project(tmp_path, items, narrated=set(items[:6]))
    plan = build_plan(root, batch_size=12)
    assert plan["batches"][0]["ready_to_render"] is False
