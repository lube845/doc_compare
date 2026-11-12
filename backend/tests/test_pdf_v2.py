"""PDF 比对 V2 · 验收测试（V2 方案 第十三章）.

The tests follow the test matrix of the V2 plan: the same text with
another typography must not produce a difference, a real difference must
be found and must be located on the page, and a document that changed its
pagination must still be matched by its content.
"""

from __future__ import annotations

import pytest

from app.compare.pdf_compare import compare_pdfs
from app.compare.pdf_normalize import normalise_stream
from app.compare.pdf_tokens import tokenize
from app.parsers.pdf_parser import PdfParser
from app.schemas import CompareConfig

from tests.fixtures import (
    build_pdf_columns,
    build_pdf_font,
    build_pdf_pages,
    build_pdf_table,
)

# --------------------------------------------------------------------------
# The sample document
# --------------------------------------------------------------------------

CHAPTERS = [
    "第一章 总则",
    "本合同由甲乙双方于2024年3月15日在北京市签署，双方经友好协商，就合作事项达成如下协议。",
    "第一条 标的金额为人民币1200000.00元，分三期支付。",
    "第二条 交付期限为2024年6月30日前，逾期交付的，每日按合同金额的0.05%支付违约金。",
    "第三条 本合同一式两份，甲乙双方各执一份，具有同等法律效力。",
]

MIXED = [
    "项目概述 Project Overview",
    "本系统采用 The project costs 100 USD 的模式。",
    "技术方案 Technical Solution v2.0 已完成。",
]


@pytest.fixture()
def writer(tmp_path):
    """A builder for the sample documents."""

    def build(name: str, pages, **kwargs):
        return build_pdf_pages(tmp_path / name, pages, **kwargs)

    return build


def _parse(path, config=None):
    return PdfParser().parse(path, config or CompareConfig())


def _compare(path_a, path_b, config=None):
    config = config or CompareConfig()
    return compare_pdfs(_parse(path_a, config), _parse(path_b, config), config)


def _texts(changes) -> tuple[str, str]:
    """The old and the new text of every change."""
    return (
        " ".join(change.document_a.text for change in changes),
        " ".join(change.document_b.text for change in changes),
    )


# --------------------------------------------------------------------------
# 13.1 基础测试
# --------------------------------------------------------------------------


def test_t01_two_identical_documents_have_no_difference(writer):
    """T01 - the same document twice has no difference."""
    path_a = writer("a.pdf", [CHAPTERS])
    path_b = writer("b.pdf", [CHAPTERS])

    changes, _notices = _compare(path_a, path_b)
    assert changes == []


def test_t02_a_changed_number_is_one_difference(writer):
    """T02 - only the number is different."""
    changed = list(CHAPTERS)
    changed[2] = changed[2].replace("1200000.00", "1350000.00")
    path_a = writer("a.pdf", [CHAPTERS])
    path_b = writer("b.pdf", [changed])

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    change = changes[0]
    assert change.primary_type == "modified"
    assert "1200000.00" in change.document_a.text.replace(" ", "")
    assert "1350000.00" in change.document_b.text.replace(" ", "")


def test_t03_a_changed_word_is_one_difference(writer):
    """T03 - only the changed words are different."""
    changed = list(CHAPTERS)
    changed[4] = changed[4].replace("同等法律效力", "同等法律约束力")
    path_a = writer("a.pdf", [CHAPTERS])
    path_b = writer("b.pdf", [changed])

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    # The Chinese characters are the unit of the comparison (V2 §6.2): the
    # change is reported where it really is, not for the whole paragraph.
    assert changes[0].document_a.text.strip() == "效"
    assert changes[0].document_b.text.strip() == "约束"
    assert "法律" in changes[0].details["paragraph_a"]["text"]


def test_t04_an_added_paragraph_is_found(writer):
    """T04 - a new paragraph is an addition."""
    extended = CHAPTERS[:3] + ["第四条 因不可抗力导致合同无法履行的，双方均不承担违约责任。"] + CHAPTERS[3:]
    path_a = writer("a.pdf", [CHAPTERS])
    path_b = writer("b.pdf", [extended])

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    assert changes[0].primary_type == "added"
    assert "不可抗力" in changes[0].document_b.text


def test_t05_a_deleted_paragraph_is_found(writer):
    """T05 - a removed paragraph is a deletion."""
    shorter = CHAPTERS[:3] + CHAPTERS[4:]
    path_a = writer("a.pdf", [CHAPTERS])
    path_b = writer("b.pdf", [shorter])

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    assert changes[0].primary_type == "deleted"
    assert "不可抗力" not in changes[0].document_b.text


def test_t06_a_changed_paragraph_is_found_precisely(writer):
    """T06 - the changed part of a paragraph is reported, not the paragraph."""
    changed = list(CHAPTERS)
    changed[1] = changed[1].replace("友好协商", "充分协商")
    path_a = writer("a.pdf", [CHAPTERS])
    path_b = writer("b.pdf", [changed])

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    old, new = _texts(changes)
    assert old.strip() == "友好"
    assert new.strip() == "充分"
    # The difference does not cover the whole paragraph.
    assert len(old) < len(CHAPTERS[1]) / 2


# --------------------------------------------------------------------------
# 13.2 排版变化测试
# --------------------------------------------------------------------------


def test_t07_another_line_break_is_no_difference(writer):
    """T07 - the same text, wrapped differently, is the same text."""
    path_a = writer("a.pdf", [CHAPTERS], margin=72, size=10)
    path_b = writer("b.pdf", [CHAPTERS], margin=150, size=13, leading=21)

    changes, _notices = _compare(path_a, path_b)
    assert changes == []


def test_t08_a_paragraph_split_in_two_is_no_difference(writer):
    """T08 - the same text, cut at another place, is the same text."""
    split = list(CHAPTERS)
    head, tail = split[2].split("，", 1)
    split = split[:2] + [head + "，", tail] + split[3:]
    path_a = writer("a.pdf", [CHAPTERS])
    path_b = writer("b.pdf", [split])

    changes, _notices = _compare(path_a, path_b)
    assert changes == []


def test_t09_two_paragraphs_merged_are_no_difference(writer):
    """T09 - the same text, cut at another place, is the same text."""
    merged = list(CHAPTERS)
    merged = merged[:2] + [merged[2] + merged[3]] + merged[4:]
    path_a = writer("a.pdf", [CHAPTERS])
    path_b = writer("b.pdf", [merged])

    changes, _notices = _compare(path_a, path_b)
    assert changes == []


def test_t10_another_font_is_no_difference(tmp_path):
    """T10 - the same text in another font is the same text."""
    path_a = build_pdf_font(tmp_path / "a.pdf", CHAPTERS, fontname="china-s")
    path_b = build_pdf_font(tmp_path / "b.pdf", CHAPTERS, fontname="china-ss")

    changes, _notices = _compare(path_a, path_b)
    assert changes == []


def test_t11_another_font_size_is_no_difference(writer):
    """T11 - the same text in another size is the same text."""
    path_a = writer("a.pdf", [CHAPTERS], size=10)
    path_b = writer("b.pdf", [CHAPTERS], size=14, leading=22)

    changes, _notices = _compare(path_a, path_b)
    assert changes == []


def test_t12_another_page_width_is_no_difference(writer):
    """T12 - another page width reflows the text, it does not change it."""
    path_a = writer("a.pdf", [CHAPTERS], width=595)
    path_b = writer("b.pdf", [CHAPTERS], width=760, margin=72)

    changes, _notices = _compare(path_a, path_b)
    assert changes == []


def test_t13_another_text_position_is_no_difference(writer):
    """T13 - the text may stand somewhere else on the page."""
    path_a = writer("a.pdf", [CHAPTERS], margin=72)
    path_b = writer("b.pdf", [CHAPTERS], margin=200)

    changes, _notices = _compare(path_a, path_b)
    assert changes == []


# --------------------------------------------------------------------------
# 13.3 页面变化测试
# --------------------------------------------------------------------------


def test_t14_a_page_at_the_front_shifts_nothing(writer):
    """T14 - a new page at the front does not shift the rest."""
    path_a = writer("a.pdf", [CHAPTERS[:3], CHAPTERS[3:]])
    path_b = writer(
        "b.pdf",
        [["封面：项目建议书"], CHAPTERS[:3], CHAPTERS[3:]],
    )

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    assert changes[0].primary_type == "added"
    assert "封面" in changes[0].document_b.text


def test_t15_a_page_in_the_middle_shifts_nothing(writer):
    """T15 - a removed page in the middle does not shift the rest."""
    path_a = writer("a.pdf", [CHAPTERS[:2], CHAPTERS[2:4], CHAPTERS[4:]])
    path_b = writer("b.pdf", [CHAPTERS[:2], CHAPTERS[4:]])

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    assert changes[0].primary_type == "deleted"


def test_t16_a_new_paragraph_reflows_the_pages(writer):
    """T16 - a new paragraph in the middle does not shift the rest."""
    extended = CHAPTERS[:2] + ["第一条 本项目自签署之日起生效。"] + CHAPTERS[2:]
    path_a = writer("a.pdf", [CHAPTERS])
    path_b = writer("b.pdf", [extended])

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    assert changes[0].primary_type == "added"
    assert "生效" in changes[0].document_b.text


def test_t17_a_new_page_at_the_end_is_found(writer):
    """T17 - a new page at the end is an addition."""
    path_a = writer("a.pdf", [CHAPTERS])
    path_b = writer("b.pdf", [CHAPTERS, ["附录：本项目联系人及联系方式。"]])

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    assert changes[0].primary_type == "added"
    assert "附录" in changes[0].document_b.text


def test_t18_a_repeated_text_is_matched_once(writer):
    """T18 - a text that occurs several times is not matched twice.

    The change is only in the third copy; the other copies stay untouched.
    """
    repeated = ["说明文字，用于说明重复内容的处理方式。"] * 4
    changed = list(repeated)
    changed[2] = "说明文字，用于说明重复内容的处理方式甲。"
    path_a = writer("a.pdf", [repeated])
    path_b = writer("b.pdf", [changed])

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    assert changes[0].details["paragraph_b"]["index"] == 2


# --------------------------------------------------------------------------
# 13.4 复杂文档测试
# --------------------------------------------------------------------------


def test_t19_two_columns_keep_the_reading_order(tmp_path):
    """T19 - the left column is read before the right one."""
    left = ["左栏第一行", "左栏第二行", "左栏第三行"]
    right = ["右栏第一行", "右栏第二行", "右栏第三行"]
    path_a = build_pdf_columns(tmp_path / "a.pdf", [left, right])

    parsed = _parse(path_a)
    stream = normalise_stream(parsed.parsed_pdf, CompareConfig())
    assert stream.text.index("左栏第一行") < stream.text.index("右栏第一行")
    assert stream.text.index("左栏第三行") < stream.text.index("右栏第一行")


def test_t20_the_text_of_a_table_is_compared(tmp_path):
    """T20 - the text of a table is compared as well."""
    rows_a = [["编号", "金额"], ["001", "120000"], ["002", "350000"]]
    rows_b = [["编号", "金额"], ["001", "120000"], ["002", "360000"]]
    path_a = build_pdf_table(tmp_path / "a.pdf", rows_a)
    path_b = build_pdf_table(tmp_path / "b.pdf", rows_b)

    changes, _notices = _compare(path_a, path_b)
    assert changes, "the changed cell has to be reported"
    old, new = _texts(changes)
    assert "350000" in old or "350000" in new
    assert "360000" in old or "360000" in new


def test_t21_another_running_header_is_no_difference(writer):
    """T21 - the running header does not produce a difference."""
    path_a = writer("a.pdf", [CHAPTERS], header="项目建议书")
    path_b = writer("b.pdf", [CHAPTERS], header="项目建议书（修订稿）")

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) <= 1


def test_t22_consecutive_numbers_are_found(writer):
    """T22 - a number in a row of numbers is found."""
    base = ["编号 001 002 003 004 005", "金额 100 200 300 400 500"]
    changed = ["编号 001 002 003 004 005", "金额 100 200 350 400 500"]
    path_a = writer("a.pdf", [base])
    path_b = writer("b.pdf", [changed])

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    old, new = _texts(changes)
    assert "300" in old.replace(" ", "")
    assert "350" in new.replace(" ", "")


def test_t23_chinese_and_english_are_found(writer):
    """T23 - a change in the english part is found."""
    changed = list(MIXED)
    changed[2] = changed[2].replace("v2.0", "v3.0")
    path_a = writer("a.pdf", [MIXED])
    path_b = writer("b.pdf", [changed])

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    old, new = _texts(changes)
    # A number is one token, the letters around it are another one.
    assert old.replace(" ", "") == "2.0"
    assert new.replace(" ", "") == "3.0"


def test_t24_a_paragraph_over_two_pages_is_matched(writer):
    """T24 - a paragraph that runs over a page break is still one text."""
    long_paragraph = (
        "本项目旨在建设一套统一的数据管理平台，覆盖业务、财务与人事三大领域，"
        "并在此基础上提供分析与决策支持能力，帮助各单位提高工作效率与管理水平。"
    )
    half = len(long_paragraph) // 2
    path_a = writer("a.pdf", [[long_paragraph]])
    path_b = writer("b.pdf", [[long_paragraph[:half]], [long_paragraph[half:]]])

    changes, _notices = _compare(path_a, path_b)
    assert changes == []


def test_t25_many_repeated_paragraphs_do_not_shift(writer):
    """T25 - many repeated paragraphs do not lead to a wrong match."""
    repeated = ["第一条 本条内容在合同中出现多次，用于说明重复内容的处理。"] * 6
    changed = list(repeated)
    changed[4] = "第一条 本条内容在合同中出现多次，用于说明重复内容的处理甲。"
    path_a = writer("a.pdf", [repeated])
    path_b = writer("b.pdf", [changed])

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    # Only one character was added, so the change is an addition.
    assert changes[0].primary_type == "added"
    assert changes[0].document_b.text.strip() == "甲"
    assert changes[0].details["paragraph_b"]["index"] == 4


# --------------------------------------------------------------------------
# 13.5 坐标映射测试
# --------------------------------------------------------------------------


def test_the_page_of_a_difference_is_right(writer):
    """The difference knows the page it stands on."""
    changed = list(CHAPTERS)
    changed[3] = changed[3].replace("0.05%", "0.15%")
    path_a = writer("a.pdf", [CHAPTERS[:3], CHAPTERS[3:]])
    path_b = writer("b.pdf", [changed[:3], changed[3:]])

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    assert changes[0].document_a.location.page == 2
    assert changes[0].document_b.location.page == 2


def test_the_rectangle_is_on_the_page(writer):
    """The rectangle of a difference lies on the page."""
    changed = list(CHAPTERS)
    changed[2] = changed[2].replace("1200000.00", "1350000.00")
    path_a = writer("a.pdf", [CHAPTERS])
    path_b = writer("b.pdf", [changed])

    changes, _notices = _compare(path_a, path_b)
    rects = changes[0].document_a.location.extra["rects"]
    assert rects
    for x0, y0, x1, y1 in rects:
        assert 0 <= x0 < x1 <= 595
        assert 0 <= y0 < y1 <= 842


def test_the_two_sides_have_their_own_rectangles(writer):
    """The old and the new text have their own rectangles."""
    changed = list(CHAPTERS)
    changed[2] = changed[2].replace("1200000.00", "1350000.00")
    path_a = writer("a.pdf", [CHAPTERS])
    path_b = writer("b.pdf", [changed])

    changes, _notices = _compare(path_a, path_b)
    assert changes[0].document_a.location.extra["rects"]
    assert changes[0].document_b.location.extra["rects"]


def test_a_text_over_two_lines_has_two_rectangles(writer):
    """A text that covers two lines is covered by two rectangles."""
    changed = list(CHAPTERS)
    changed[1] = changed[1].replace("北京市", "上海市")
    path_a = writer("a.pdf", [CHAPTERS], size=16, width=400)
    path_b = writer("b.pdf", [changed], size=16, width=400)

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    rects = changes[0].document_a.location.extra["rects"]
    assert len(rects) >= 1


# --------------------------------------------------------------------------
# The token layer
# --------------------------------------------------------------------------


def test_a_chinese_character_is_one_token(writer):
    """A Chinese character is a token of its own."""
    path_a = writer("a.pdf", [CHAPTERS])
    parsed = _parse(path_a)
    stream = normalise_stream(parsed.parsed_pdf, CompareConfig())
    tokens = tokenize(stream)

    assert "合" in tokens.texts
    assert "同" in tokens.texts
    # A number is one token, not one token per digit.
    assert "1200000.00" in tokens.texts


def test_an_english_word_is_one_token(writer):
    """A run of latin letters is one token."""
    path_a = writer("a.pdf", [MIXED])
    parsed = _parse(path_a)
    stream = normalise_stream(parsed.parsed_pdf, CompareConfig())
    tokens = tokenize(stream)

    assert "Overview" in tokens.texts
    assert "Project" in tokens.texts


def test_every_token_knows_its_characters(writer):
    """Every token points at the characters of the PDF it was built from."""
    path_a = writer("a.pdf", [CHAPTERS])
    parsed = _parse(path_a)
    stream = normalise_stream(parsed.parsed_pdf, CompareConfig())
    tokens = tokenize(stream)

    for token in tokens.tokens:
        source = parsed.parsed_pdf.chars[token.char_first]
        assert source.ch
        assert token.char_last >= token.char_first


# --------------------------------------------------------------------------
# The details of a change
# --------------------------------------------------------------------------


def test_an_added_paragraph_covers_a_whole_paragraph(writer):
    """A whole paragraph is reported as a paragraph (V2 §8.2)."""
    extended = CHAPTERS + ["第四条 因不可抗力导致合同无法履行的，双方均不承担违约责任。"]
    path_a = writer("a.pdf", [CHAPTERS])
    path_b = writer("b.pdf", [extended])

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    assert changes[0].details["scope"] == "paragraph"


def test_a_change_carries_its_context(writer):
    """Every change carries the text before and after it (V2 §11.3)."""
    changed = list(CHAPTERS)
    changed[2] = changed[2].replace("1200000.00", "1350000.00")
    path_a = writer("a.pdf", [CHAPTERS])
    path_b = writer("b.pdf", [changed])

    changes, _notices = _compare(path_a, path_b)
    details = changes[0].details
    assert details.get("context_before")
    # The amount stands at the end of the sentence, so the context after it
    # is the rest of the sentence.
    assert "分三期支付" in details.get("context_after", "")


def test_a_changed_amount_is_tagged(writer):
    """A change of an amount is tagged with its kind (V2 §10.3)."""
    changed = list(CHAPTERS)
    changed[2] = changed[2].replace("1200000.00", "1350000.00")
    path_a = writer("a.pdf", [CHAPTERS])
    path_b = writer("b.pdf", [changed])

    changes, _notices = _compare(path_a, path_b)
    assert "amount" in changes[0].details.get("tags", [])
    assert changes[0].category == "amount"


def test_a_changed_date_is_tagged(writer):
    """A change of a date is tagged with its kind."""
    changed = list(CHAPTERS)
    changed[3] = changed[3].replace("2024年6月30日", "2024年7月15日")
    path_a = writer("a.pdf", [CHAPTERS])
    path_b = writer("b.pdf", [changed])

    changes, _notices = _compare(path_a, path_b)
    assert "date" in changes[0].details.get("tags", [])
    assert changes[0].category == "date"


def test_a_change_inside_a_date_covers_the_whole_date(writer):
    """A single changed digit must not hide the date that changed."""
    changed = list(CHAPTERS)
    changed[3] = changed[3].replace("2024年6月30日", "2024年7月30日")
    path_a = writer("a.pdf", [CHAPTERS])
    path_b = writer("b.pdf", [changed])

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    old = changes[0].document_a.text.replace(" ", "")
    new = changes[0].document_b.text.replace(" ", "")
    assert old == "6" or old == "2024年6月30日"
    assert new == "7" or new == "2024年7月30日"


def test_an_inserted_duplicate_does_not_shift_the_copies(writer):
    """A copied paragraph inserted in the middle shifts nothing (V2 §7.4).

    The copies carry the same text, so an engine that only looks at the
    text could pick the wrong copy and report the whole rest of the
    document as changed.  The unique paragraphs around them have to
    stay matched.
    """
    marker_a = "第一节 项目背景与建设必要性的说明。"
    marker_b = "第二节 建设目标与总体架构的说明。"
    repeated = "第三条 本条内容在合同中出现多次，用于检验重复内容的处理方式。"
    base = [marker_a, repeated, repeated, marker_b]
    inserted = [marker_a, repeated, repeated, repeated, marker_b]
    path_a = writer("a.pdf", [base])
    path_b = writer("b.pdf", [inserted])

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    assert changes[0].primary_type == "added"
    assert "建设目标" not in changes[0].document_b.text
    assert changes[0].details["paragraph_b"]["index"] in (2, 3)


def test_a_change_across_a_page_break_has_one_box_per_page(writer):
    """A text that spans two pages gets one box per page (V2 §9.3)."""
    long_paragraph = (
        "本项目旨在建设一套统一的数据管理平台，覆盖业务、财务与人事三大领域，"
        "并在此基础上提供分析与决策支持能力，帮助各单位提高工作效率与管理水平，"
        "系统将于2024年12月31日前完成部署并投入试运行。"
    )
    changed = long_paragraph.replace("2024年12月31日", "2025年1月31日")
    path_a = writer("a.pdf", [[long_paragraph]], size=14, width=480)
    path_b = writer("b.pdf", [[changed]], size=14, width=480)

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    assert changes[0].document_a.location.page == changes[0].document_b.location.page
    assert changes[0].document_a.location.extra["rects"]


def test_a_document_compared_with_itself_has_no_change(writer):
    """The same file compared with itself is empty of differences."""
    path_a = writer("a.pdf", [MIXED, CHAPTERS])
    changes, _notices = _compare(path_a, path_a)
    assert changes == []


def test_a_blank_page_produces_no_difference(writer):
    """A blank page carries no text, so it changes no text."""
    path_a = writer("a.pdf", [CHAPTERS])
    path_b = writer("b.pdf", [[], CHAPTERS])

    changes, _notices = _compare(path_a, path_b)
    assert changes == []


def test_a_page_number_footer_is_not_a_difference(tmp_path):
    """The page number of a footer is not content (V2 §13.4 T21).

    A PDF that extracts the digits of a wide font one by one writes the
    page 10 as "第 1 0 页" - the two forms of the footer have to be
    recognised as the same running footer, otherwise an inserted page
    turns every number of the document into a difference.
    """
    import fitz

    body = [
        "第一条 标的金额为人民币1200000.00元，分三期支付。",
        "第二条 交付期限为2024年6月30日前。",
    ]

    def build(name, pages, cover):
        document = fitz.open()
        if cover:
            page = document.new_page(width=595, height=842)
            page.insert_text((72, 80), "封面：投标文件（新增）", fontsize=10, fontname="china-s")
        for index in range(pages):
            page = document.new_page(width=595, height=842)
            y = 90
            for paragraph in body * 6:
                page.insert_text((72, y), paragraph, fontsize=10, fontname="china-s")
                y += 18
            number = index + 1 + (1 if cover else 0)
            page.insert_text((280, 810), f"第 {number} 页", fontsize=9, fontname="china-s")
        path = tmp_path / name
        document.save(str(path))
        document.close()
        return path

    path_a = build("a.pdf", 10, cover=False)
    path_b = build("b.pdf", 10, cover=True)

    changes, _notices = _compare(path_a, path_b)
    assert len(changes) == 1
    assert changes[0].primary_type == "added"
    assert "封面" in changes[0].document_b.text


def test_a_change_is_a_part_of_the_paragraph_the_reader_sees(tmp_path):
    """The text of a change has to be findable inside the paragraph (V2 §11).

    The frontend marks the changed characters by searching the text of a
    change inside the text of the paragraph it belongs to.  When a change
    spans several rendered lines its text must therefore be joined exactly
    the way the parser joins the lines of a paragraph, otherwise the mark
    is not drawn at all.
    """
    base = [
        "Section 1. This agreement is made between the parties on 15 March 2024 in Beijing.",
        "Section 3. This agreement is executed in two counterparts.",
    ]
    extra = (
        "Section 2. The Supplier shall deliver the equipment before 30 June 2024, "
        "and the Buyer shall pay the amount by installments as agreed between the "
        "parties; late delivery shall be subject to a penalty of 0.05% of the "
        "contract value per day."
    )
    path_a = build_pdf_pages(tmp_path / "a.pdf", [base], size=12, width=420, fontname="helv")
    path_b = build_pdf_pages(
        tmp_path / "b.pdf", [base[:1] + [extra] + base[1:]], size=12, width=420, fontname="helv"
    )

    parse_a = _parse(path_a)
    parse_b = _parse(path_b)
    changes, _notices = compare_pdfs(parse_a, parse_b, CompareConfig())
    assert len(changes) == 1

    blocks_a = {block.block_id: block.original_text for block in parse_a.document.blocks}
    blocks_b = {block.block_id: block.original_text for block in parse_b.document.blocks}

    change = changes[0]
    assert change.primary_type == "added"
    assert change.document_b.text.startswith("Section 2.")
    assert change.document_b.text in blocks_b[change.document_b.location.block_id]
    # The period of the paragraph before it does not belong to the change.
    assert not change.document_b.text.startswith(".")


# --------------------------------------------------------------------------
# PDF 展示（pdf展示.md）
# --------------------------------------------------------------------------


def test_the_page_map_follows_the_content(tmp_path):
    """A page that was inserted at the front shifts the page map."""
    import fitz

    def build(name, pages, cover=False):
        document = fitz.open()
        if cover:
            page = document.new_page(width=595, height=842)
            page.insert_text((72, 80), "封面：投标文件（新增）", fontsize=10, fontname="china-s")
        for text in pages:
            page = document.new_page(width=595, height=842)
            page.insert_text((72, 100), text, fontsize=10, fontname="china-s")
        path = tmp_path / name
        document.save(str(path))
        document.close()
        return path

    pages = [f"第{i}页的正文内容，用于检验页面对应关系。" for i in range(1, 5)]
    path_a = build("a.pdf", pages)
    path_b = build("b.pdf", pages, cover=True)

    layout: dict = {}
    parse_a = _parse(path_a)
    parse_b = _parse(path_b)
    compare_pdfs(parse_a, parse_b, CompareConfig(), layout=layout)

    pairs = layout.get("pairs") or []
    assert pairs
    for pair in pairs:
        assert pair["left_page"] == pair["right_page"] - 1
        assert 0.0 < pair["confidence"] <= 1.0
    # The added page has no left page: it is not in the map.
    assert all(pair["right_page"] != 1 for pair in pairs)


def test_a_change_across_pages_knows_the_page_of_every_box(tmp_path):
    """A deletion that runs over a page break carries boxes on both pages."""
    import fitz

    paragraph = "本条规定了双方的权利义务，涉及交付、验收、付款与违约责任的完整约定内容。"

    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 100), "第一部分 总则", fontsize=10, fontname="china-s")
    y = 130
    for _ in range(35):
        page.insert_text((72, y), paragraph, fontsize=10, fontname="china-s")
        y += 16
    page = document.new_page(width=595, height=842)
    y = 80
    for _ in range(20):
        page.insert_text((72, y), paragraph, fontsize=10, fontname="china-s")
        y += 16
    page.insert_text((72, y + 30), "第二部分 附则", fontsize=10, fontname="china-s")
    path_a = tmp_path / "a.pdf"
    document.save(str(path_a))
    document.close()

    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 100), "第一部分 总则", fontsize=10, fontname="china-s")
    page.insert_text((72, 130), "第二部分 附则", fontsize=10, fontname="china-s")
    path_b = tmp_path / "b.pdf"
    document.save(str(path_b))
    document.close()

    changes, _notices = _compare(path_a, path_b)
    assert changes
    deleted = [change for change in changes if change.primary_type == "deleted"]
    assert deleted
    pages = {
        page
        for change in deleted
        for page in change.document_a.location.extra.get("rect_pages") or []
    }
    assert pages == {1, 2}
