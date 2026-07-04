"""library/<name>/manga.json — the manga's source-link record.

Written by `mangaeasy download`, surfaced by `library-list`; answers
"where did this manga come from?" without re-reading config.json history.
"""

import json

from mangaeasy.download.mangadex import (
    load_manga_json,
    manga_url,
    merge_manga_record,
    update_manga_json,
)
from mangaeasy.library_scan import scan_library

UUID = "2d63ef8c-eae6-44b4-a300-595b7de11516"
SLUG_URL = f"https://mangadex.org/title/{UUID}/kanojo-wo-dere-saseru"


def test_new_record_from_scratch():
    rec = merge_manga_record(
        {},
        name="MyManga",
        manga_id=UUID,
        lang="en",
        chapter_str="01",
        chapter_id="ch-uuid",
        pages=42,
        source_url=SLUG_URL,
        title="My Manga",
        when="2026-07-04T00:00:00+00:00",
    )
    assert rec["url"] == manga_url(UUID)
    assert rec["source"] == "mangadex"
    assert rec["source_url"] == SLUG_URL  # user's original link kept
    assert rec["title"] == "My Manga"
    assert rec["chapters"]["01"] == {
        "chapter_id": "ch-uuid",
        "language": "en",
        "pages": 42,
        "downloaded_at": "2026-07-04T00:00:00+00:00",
    }


def test_bare_uuid_source_is_not_stored_as_source_url():
    rec = merge_manga_record(
        {}, name="M", manga_id=UUID, lang="en", chapter_str="01",
        chapter_id="c", pages=1, source_url=UUID,
    )
    assert "source_url" not in rec
    assert rec["url"] == manga_url(UUID)


def test_second_chapter_merges_and_sorts_without_losing_title():
    first = merge_manga_record(
        {}, name="M", manga_id=UUID, lang="en", chapter_str="02",
        chapter_id="c2", pages=30, title="Kept Title",
    )
    second = merge_manga_record(
        first, name="M", manga_id=UUID, lang="en", chapter_str="01",
        chapter_id="c1", pages=42, title=None,  # e.g. offline title fetch
    )
    assert second["title"] == "Kept Title"
    assert list(second["chapters"]) == ["01", "02"]
    assert second["chapters"]["02"]["chapter_id"] == "c2"


def test_update_manga_json_round_trips(tmp_path):
    path = update_manga_json(
        tmp_path, name="M", manga_id=UUID, lang="en", chapter_str="01",
        chapter_id="c1", pages=42,
    )
    assert path == tmp_path / "manga.json"
    assert load_manga_json(tmp_path)["manga_id"] == UUID
    # corrupt file degrades to empty, not a crash
    path.write_text("{not json", encoding="utf-8")
    assert load_manga_json(tmp_path) == {}


def test_library_list_surfaces_manga_json(tmp_path):
    proj = tmp_path / "library" / "myproj"
    (proj / "01" / "panels").mkdir(parents=True)
    (proj / "manga.json").write_text(
        json.dumps({"url": manga_url(UUID), "title": "My Manga"}),
        encoding="utf-8",
    )
    project = scan_library(tmp_path)["projects"][0]
    assert project["manga"]["url"] == manga_url(UUID)


def test_library_list_manga_is_none_when_absent(tmp_path):
    proj = tmp_path / "library" / "myproj"
    (proj / "01" / "panels").mkdir(parents=True)
    assert scan_library(tmp_path)["projects"][0]["manga"] is None
