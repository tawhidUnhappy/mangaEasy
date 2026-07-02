"""Item-selection parsing (`--items 01 02 05-08`, `--item-range 01-12`)."""

from mangaeasy.video_pipeline.common import chunk_list, expand_item_tokens, merge_item_selection


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
