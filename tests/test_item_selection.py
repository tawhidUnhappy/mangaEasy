"""Item-selection parsing (`--items 01 02 05-08`, `--item-range 01-12`)."""

from mangaeasy.video_pipeline.common import (
    chunk_list,
    expand_item_tokens,
    item_dirs,
    item_value,
    merge_item_selection,
)


def test_empty_tokens_mean_no_selection():
    assert expand_item_tokens(None) is None
    assert expand_item_tokens([]) is None


def test_single_numbers_are_zero_padded():
    assert expand_item_tokens(["1", "07", "12"]) == ["01", "07", "12"]


def test_ranges_expand_inclusive():
    assert expand_item_tokens(["05-08"]) == ["05", "06", "07", "08"]
    assert expand_item_tokens(["1..3"]) == ["01", "02", "03"]
    assert expand_item_tokens(["1:3"]) == ["01", "02", "03"]


def test_descending_range_expands_backwards():
    assert expand_item_tokens(["03-01"]) == ["03", "02", "01"]


def test_comma_separated_tokens_and_dedup():
    assert expand_item_tokens(["01,02", "02-04"]) == ["01", "02", "03", "04"]


def test_non_numeric_tokens_pass_through():
    assert expand_item_tokens(["extras"]) == ["extras"]


def test_merge_items_and_range():
    assert merge_item_selection(["01"], "03-04") == ["01", "03", "04"]
    assert merge_item_selection(None, None) is None


def test_chunk_list_contiguous_and_nonempty():
    assert chunk_list([1, 2, 3, 4, 5], 2) == [[1, 2, 3], [4, 5]]
    assert chunk_list([1, 2], 1) == [[1, 2]]
    assert chunk_list([1], 4) == [[1]]
    # More shards than items must not produce empty chunks
    for chunk in chunk_list([1, 2, 3], 8):
        assert chunk


def _mk(root, *names):
    for name in names:
        (root / name / "panels").mkdir(parents=True)
    return root


def test_item_value_parses_full_numeric_value():
    assert item_value("02") == 2.0
    assert item_value("2.1") == 2.1
    assert item_value("9.5") == 9.5


def test_integer_token_never_selects_decimal_siblings(tmp_path):
    # `--items 02` used to drag 2.1/2.2 along (item_number collision).
    _mk(tmp_path, "01", "02", "2.1", "2.2", "9.5")
    assert [p.name for p in item_dirs(tmp_path, ["02"])] == ["02"]
    assert [p.name for p in item_dirs(tmp_path, ["2.1"])] == ["2.1"]
    assert [p.name for p in item_dirs(tmp_path, ["09"])] == []


def test_decimal_items_sort_by_value(tmp_path):
    _mk(tmp_path, "01", "10", "2.1", "02", "9.5")
    assert [p.name for p in item_dirs(tmp_path)] == ["01", "02", "2.1", "9.5", "10"]
