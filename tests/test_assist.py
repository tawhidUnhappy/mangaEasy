"""Pure-logic tests for the assist package (no model, no server)."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from mediaconductor.assist.characters import (
    load_characters,
    registry_prompt_block,
    validate_registry,
)
from mediaconductor.assist.narrate import (
    chunk_prompt,
    list_panels,
    load_transcript,
    merge_chunk_entries,
)
from mediaconductor.panels.style_detect import style_guard
from mediaconductor.tools.gemma import parse_json_reply
from mediaconductor.tools.install import TOOLS, llama_release_asset


# ── character registry ───────────────────────────────────────────────────────

def _registry() -> dict:
    return {
        "draft": False,
        "characters": [
            {"name": "Ren", "aliases": ["the swordsman"],
             "appearance": "silver hair, red scarf", "role": "protagonist"},
            {"name": "Mina", "appearance": "black braid, archer's gloves"},
        ],
    }


def test_validate_registry_accepts_good_and_flags_bad():
    assert validate_registry(_registry()) == []
    bad = {"characters": [{"name": ""}, {"name": "Ren"}, {"name": "ren"}]}
    problems = validate_registry(bad)
    assert any("missing name" in p for p in problems)
    assert any("duplicate" in p for p in problems)
    assert any("missing appearance" in p for p in problems)


def test_registry_prompt_block_lists_names_and_handles_absence():
    block = registry_prompt_block(_registry())
    assert "Ren" in block and "Mina" in block and "aka the swordsman" in block
    absent = registry_prompt_block(None)
    assert "NEVER invent names" in absent


def test_load_characters_roundtrip_and_rejects_garbage(tmp_path: Path):
    (tmp_path / "characters.json").write_text(json.dumps(_registry()), encoding="utf-8")
    loaded = load_characters(tmp_path)
    assert loaded is not None and loaded["characters"][0]["name"] == "Ren"
    (tmp_path / "characters.json").write_text("not json", encoding="utf-8")
    assert load_characters(tmp_path) is None


# ── narrate-auto chunk logic ─────────────────────────────────────────────────

def _panels(tmp_path: Path, names: list[str]) -> list[Path]:
    panels_dir = tmp_path / "01" / "panels"
    panels_dir.mkdir(parents=True)
    for name in names:
        Image.new("RGB", (100, 140), "white").save(panels_dir / name)
    return list_panels(tmp_path / "01")


def test_list_panels_sorted_and_transcript_loaded(tmp_path: Path):
    panels = _panels(tmp_path, ["ch01_002.jpg", "ch01_001.jpg"])
    assert [p.name for p in panels] == ["ch01_001.jpg", "ch01_002.jpg"]
    (tmp_path / "01" / "transcript.json").write_text(
        json.dumps([{"image": "ch01_001.jpg", "ocr": "Hello"}]), encoding="utf-8")
    assert load_transcript(tmp_path / "01") == {"ch01_001.jpg": "Hello"}


def test_chunk_prompt_lists_panels_in_order(tmp_path: Path):
    panels = _panels(tmp_path, ["a.jpg", "b.jpg"])
    prompt = chunk_prompt(panels, {"a.jpg": "Hi"}, "Ren fled the gate.")
    assert prompt.index("a.jpg") < prompt.index("b.jpg")
    assert 'OCR: "Hi"' in prompt and "Story so far" in prompt


def test_merge_chunk_entries_validates_skips_and_omissions(tmp_path: Path):
    panels = _panels(tmp_path, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
    parsed = {
        "story_so_far": "Ren reached the gate.",
        "entries": [
            {"image": "a.jpg", "narration": "Ren arrives.", "emotion": "calm"},
            {"image": "b.jpg", "skip": True},
            {"image": "c.jpg", "narration": ""},          # empty -> skipped
            # d.jpg omitted by the model -> skipped with a warning
        ],
    }
    entries, skipped, story = merge_chunk_entries(panels, parsed, log=lambda *_: None)
    assert entries == [{"image": "a.jpg", "narration": "Ren arrives.", "emotion": "calm"}]
    assert skipped == ["b.jpg", "c.jpg", "d.jpg"]
    assert story == "Ren reached the gate."


def test_merge_chunk_entries_survives_unparseable_reply(tmp_path: Path):
    panels = _panels(tmp_path, ["a.jpg"])
    entries, skipped, story = merge_chunk_entries(panels, None, log=lambda *_: None)
    assert entries == [] and skipped == ["a.jpg"] and story == ""


# ── gemma runner helpers ─────────────────────────────────────────────────────

def test_parse_json_reply_handles_fences_and_prose():
    assert parse_json_reply('{"a": 1}') == {"a": 1}
    assert parse_json_reply('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_reply('Sure! Here is the JSON: {"verdict": "fix"} hope it helps') \
        == {"verdict": "fix"}
    assert parse_json_reply("no json here") is None
    assert parse_json_reply(None) is None


def test_gemma_toolspec_pins_weights_and_vision_projector():
    spec = TOOLS["gemma-4"]
    assert spec.kind == "managed_env"
    (model,) = spec.extra_models
    assert model.revision, "the GGUF snapshot must be revision-pinned"
    assert any(name.endswith(".gguf") and "mmproj" not in name
               for name in model.required_files)
    assert any("mmproj" in name for name in model.required_files)
    # include patterns must not accidentally pull the BF16/Q8/mtp variants
    assert set(model.include) == set(model.required_files)
    assert spec.adapter == "run_gemma.py"


def test_llama_release_asset_covers_supported_platforms():
    assert llama_release_asset("windows", "x64", gpu=True).endswith("win-vulkan-x64.zip")
    assert llama_release_asset("windows", "x64", gpu=False).endswith("win-cpu-x64.zip")
    assert "ubuntu-vulkan" in llama_release_asset("linux", "x64", gpu=True)
    assert llama_release_asset("linux", "x64", gpu=False).endswith("ubuntu-x64.tar.gz")
    assert "macos-arm64" in llama_release_asset("darwin", "arm64", gpu=False)
    assert llama_release_asset("linux", "arm64", gpu=False) is None


# ── style guard ──────────────────────────────────────────────────────────────

def _pages(tmp_path: Path, size: tuple[int, int], count: int = 4) -> Path:
    source = tmp_path / "download"
    source.mkdir()
    for index in range(count):
        Image.new("RGB", size, "white").save(source / f"{index:03d}.jpg")
    return source


def test_sliced_webtoon_detected_despite_page_shaped_ratios(tmp_path: Path):
    """MangaDex serves many webtoons pre-cut into page-height chunks: one
    shared width, wildly varying heights, page-band ratios. The ratio bands
    alone called this 'paged' (real incident: MAGI ran over a webtoon)."""
    from mediaconductor.panels.style_detect import measure_item, verdict_from_stats

    source = tmp_path / "download"
    source.mkdir()
    heights = [1561, 1174, 1078, 1519, 1168, 1564, 1034, 1158, 1381, 993]
    for index, height in enumerate(heights):
        Image.new("RGB", (800, height), "white").save(source / f"{index:03d}.jpg")
    stats = measure_item(source)
    assert stats["paged_fraction"] >= 0.6  # the old signal that misled the verdict
    assert verdict_from_stats(stats) == "webtoon"
    ok, message = style_guard(source, "paged")
    assert not ok and "webtoon-split" in message


def test_uniform_page_scans_still_detect_as_paged(tmp_path: Path):
    from mediaconductor.panels.style_detect import measure_item, verdict_from_stats

    source = tmp_path / "download"
    source.mkdir()
    for index in range(10):
        # Real scan sets vary by a few pixels at most.
        Image.new("RGB", (1000, 1500 + (index % 3)), "white").save(source / f"{index:03d}.jpg")
    stats = measure_item(source)
    assert verdict_from_stats(stats) == "paged"


def test_style_guard_blocks_the_wrong_splitter(tmp_path: Path):
    webtoon_pages = _pages(tmp_path, (800, 8000))
    ok, message = style_guard(webtoon_pages, "paged")
    assert not ok and "webtoon-split" in message
    ok, _ = style_guard(webtoon_pages, "webtoon")
    assert ok


def test_style_guard_allows_matching_and_uncertain(tmp_path: Path):
    paged_pages = _pages(tmp_path, (1000, 1500))
    ok, _ = style_guard(paged_pages, "paged")
    assert ok
    ok, message = style_guard(paged_pages, "webtoon")
    assert not ok and "page-split" in message
    empty = tmp_path / "empty"
    empty.mkdir()
    ok, message = style_guard(empty, "webtoon")
    assert ok and "no readable pages" in message
