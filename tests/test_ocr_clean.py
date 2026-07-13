"""clean_ocr_text strips the DeepSeek garbage classes seen in real
production transcripts while preserving real bubble text and line structure."""

from mangaeasy.ocr.ocr_clean import MAX_OCR_LENGTH, clean_ocr_text


def test_real_bubble_text_passes_through():
    text = "CHROME-KUN,\nHAPPY SIXTEENTH\nBIRTHDAY!"
    assert clean_ocr_text(text) == text


def test_chinese_no_text_placeholders_become_empty():
    # every variant observed in a real run
    for sample in (
        "（图中无可辨识的文字）",
        "（图片中没有可识别的文字内容）",
        "（图中无可提取的文字）",
        "（图中无可提取的文字内容）",
        "无文字内容。",
        "（图片中没有文字内容。）",
        "（此处有对话框）",
    ):
        assert clean_ocr_text(sample) == "", sample


def test_english_hallucination_paragraphs_dropped_but_bubbles_kept():
    text = ("I am sorry, but the image provided is a photograph of a person, "
            "not a chart or graph.\nWHAT IS YOUR PROBLEM?!")
    assert clean_ocr_text(text) == "WHAT IS YOUR PROBLEM?!"
    assert clean_ocr_text("The image is a black-and-white illustration of a tree.") == ""


def test_fake_tables_and_latex_are_stripped():
    table = "<table><tr><td>Feature</td><td>1</td></tr>" + "<tr><td>x</td></tr>" * 500 + "</table>"
    assert clean_ocr_text(table) == ""
    assert clean_ocr_text(table + "\nHUH?") == "HUH?"
    assert clean_ocr_text("\\[ F(t) = 1 \\]") == ""


def test_character_runs_collapse():
    cleaned = clean_ocr_text("A" * 400 + "\nGYAAAH!")
    assert "AAA" in cleaned and "AAAA" not in cleaned
    assert "GYAAAH!" not in cleaned or True  # GYAAAH has only 3 A's - kept
    assert cleaned.endswith("GYAAAH!")


def test_length_cap():
    cleaned = clean_ocr_text("REAL TEXT. " * 200)
    assert len(cleaned) <= MAX_OCR_LENGTH + len(" …[truncated]")
    assert cleaned.endswith("…[truncated]")


def test_none_and_empty():
    assert clean_ocr_text(None) == ""
    assert clean_ocr_text("   ") == ""
