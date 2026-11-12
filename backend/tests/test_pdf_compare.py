"""Tests of the PDF comparison (PDF 比对改造实施计划).

The tests cover the three things that decide whether the comparison of two
PDFs is useful:

* the same text with another typography must not produce a difference,
* a real difference must be found and must be located on the page,
* the parser must not split a paragraph in the middle of a number.
"""

from __future__ import annotations

import pytest

from app.compare.pdf_compare import can_stream_compare, compare_pdfs
from app.compare.pdf_normalize import normalise_stream
from app.engines.dispatcher import compare as compare_parsed
from app.parsers.pdf_layout import (
    PdfLine,
    _group_by_baseline,
    _is_heading,
    line_pitch,
)
from app.parsers.pdf_parser import PdfParser
from app.schemas import CompareConfig

from tests.fixtures import build_pdf_layout

PARAGRAPHS = [
    "第一章 总则",
    "本合同由甲乙双方于2024年3月15日在北京市签署，双方经友好协商，就合作事项达成如下协议。",
    "第一条 标的金额为人民币1200000.00元，分三期支付。",
    "第二条 交付期限为2024年6月30日前，逾期交付的，每日按合同金额的0.05%支付违约金。",
    "第三条 本合同一式两份，甲乙双方各执一份，具有同等法律效力。",
]


@pytest.fixture()
def layout_factory(tmp_path):
    def factory(name: str, paragraphs, **kwargs):
        return build_pdf_layout(tmp_path / name, paragraphs, **kwargs)

    return factory


def _parse(path, config=None):
    return PdfParser().parse(path, config or CompareConfig())


# --------------------------------------------------------------------------
# The golden case: the same text, another typography
# --------------------------------------------------------------------------


def test_same_text_other_typography_has_no_difference(layout_factory):
    """The same content with another margin, size and spacing is equal."""
    path_a = layout_factory("a.pdf", PARAGRAPHS, margin=72, size=10)
    path_b = layout_factory("b.pdf", PARAGRAPHS, margin=130, size=12, leading=19)

    changes, _ = compare_pdfs(_parse(path_a), _parse(path_b), CompareConfig())
    assert changes == []


def test_normalised_text_is_identical(layout_factory):
    """The normalised text does not depend on the typography."""
    path_a = layout_factory("a.pdf", PARAGRAPHS, margin=72, size=10)
    path_b = layout_factory("b.pdf", PARAGRAPHS, margin=130, size=12, leading=19)

    config = CompareConfig()
    stream_a = normalise_stream(_parse(path_a).parsed_pdf, config)
    stream_b = normalise_stream(_parse(path_b).parsed_pdf, config)
    assert stream_a.text == stream_b.text
    assert stream_a.text


# --------------------------------------------------------------------------
# Real differences
# --------------------------------------------------------------------------


def test_a_changed_number_is_found(layout_factory):
    """A changed amount is one difference, not two paragraphs."""
    changed = list(PARAGRAPHS)
    changed[2] = changed[2].replace("1200000.00", "1350000.00")
    path_a = layout_factory("a.pdf", PARAGRAPHS, margin=72, size=10)
    path_c = layout_factory("c.pdf", changed, margin=72, size=10)

    changes, _ = compare_pdfs(_parse(path_a), _parse(path_c), CompareConfig())
    assert len(changes) == 1
    change = changes[0]
    assert change.primary_type == "modified"
    assert "2" in change.document_a.text and "3" in change.document_b.text


def test_a_changed_number_shows_the_whole_number(layout_factory):
    """The change covers the whole number, not the single digit."""
    changed = list(PARAGRAPHS)
    changed[2] = changed[2].replace("1200000.00", "1350000.00")
    path_a = layout_factory("a.pdf", PARAGRAPHS, margin=72, size=10)
    path_c = layout_factory("c.pdf", changed, margin=72, size=10)

    changes, _ = compare_pdfs(_parse(path_a), _parse(path_c), CompareConfig())
    old = changes[0].document_a.text.replace(" ", "")
    new = changes[0].document_b.text.replace(" ", "")
    assert "1200000.00" in old
    assert "1350000.00" in new


def test_a_paragraph_cut_at_another_place_is_no_difference(layout_factory):
    """The same text, cut into paragraphs at another place, is equal.

    This is the case the old engine could not solve: the blocks are
    different, the text is not.
    """
    split = list(PARAGRAPHS)
    head, tail = split[2].split("，", 1)
    split = split[:2] + [head + "，", tail] + split[3:]

    path_a = layout_factory("a.pdf", PARAGRAPHS, margin=72, size=10)
    path_b = layout_factory("b.pdf", split, margin=72, size=10)

    changes, _ = compare_pdfs(_parse(path_a), _parse(path_b), CompareConfig())
    assert changes == []


def test_a_changed_number_is_located_on_the_page(layout_factory):
    """The difference knows the rectangle it occupies."""
    changed = list(PARAGRAPHS)
    changed[2] = changed[2].replace("1200000.00", "1350000.00")
    path_a = layout_factory("a.pdf", PARAGRAPHS, margin=72, size=10)
    path_c = layout_factory("c.pdf", changed, margin=72, size=10)

    changes, _ = compare_pdfs(_parse(path_a), _parse(path_c), CompareConfig())
    rects = changes[0].document_a.location.extra.get("rects")
    assert rects, "the difference must carry a rectangle"
    assert changes[0].document_a.location.page == 1
    x0, y0, x1, y1 = rects[0]
    assert x0 < x1 and y0 < y1


def test_an_added_paragraph_is_found(layout_factory):
    """A new paragraph is reported as an addition."""
    extended = PARAGRAPHS[:3] + ["第四条 因不可抗力导致合同无法履行的，双方均不承担违约责任。"] + PARAGRAPHS[3:]
    path_a = layout_factory("a.pdf", PARAGRAPHS, margin=72, size=10)
    path_b = layout_factory("b.pdf", extended, margin=72, size=10)

    changes, _ = compare_pdfs(_parse(path_a), _parse(path_b), CompareConfig())
    assert len(changes) == 1
    assert changes[0].primary_type == "added"
    assert "不可抗力" in changes[0].document_b.text


def test_a_deleted_paragraph_is_found(layout_factory):
    """A removed paragraph is reported as a deletion."""
    shorter = PARAGRAPHS[:3] + PARAGRAPHS[4:]
    path_a = layout_factory("a.pdf", PARAGRAPHS, margin=72, size=10)
    path_b = layout_factory("b.pdf", shorter, margin=72, size=10)

    changes, _ = compare_pdfs(_parse(path_a), _parse(path_b), CompareConfig())
    assert len(changes) == 1
    assert changes[0].primary_type == "deleted"


def test_the_dispatcher_uses_the_stream_path(layout_factory):
    """Two PDFs are compared through their character stream."""
    path_a = layout_factory("a.pdf", PARAGRAPHS, margin=72, size=10)
    path_b = layout_factory("b.pdf", PARAGRAPHS, margin=72, size=10)
    parse_a = _parse(path_a)
    parse_b = _parse(path_b)
    assert can_stream_compare(parse_a, parse_b)

    _mode, changes, _notices = compare_parsed(parse_a, parse_b, CompareConfig())
    assert changes == []


def test_the_stream_path_can_be_switched_off(layout_factory):
    """The old engine stays available as a fallback."""
    changed = list(PARAGRAPHS)
    changed[2] = changed[2].replace("1200000.00", "1350000.00")
    path_a = layout_factory("a.pdf", PARAGRAPHS, margin=72, size=10)
    path_b = layout_factory("b.pdf", changed, margin=72, size=10)

    config = CompareConfig(pdf_stream_mode=False)
    _mode, changes, _notices = compare_parsed(_parse(path_a), _parse(path_b), config)
    assert changes, "the old engine has to report the difference as well"


# --------------------------------------------------------------------------
# The normalisation
# --------------------------------------------------------------------------


def test_the_mapping_points_at_the_original_characters(layout_factory):
    """Every character of the normalised text knows its original."""
    path_a = layout_factory("a.pdf", PARAGRAPHS, margin=72, size=10)
    parsed = _parse(path_a)
    config = CompareConfig()
    stream = normalise_stream(parsed.parsed_pdf, config)

    assert stream.index
    assert len(stream.index) == len(stream.text)
    for position in range(0, len(stream.text), max(1, len(stream.text) // 50)):
        char = parsed.parsed_pdf.chars[stream.index[position]]
        assert char.ch.strip()


def test_the_whitespace_is_removed(layout_factory):
    """The spacing of the page does not belong to the text."""
    path_a = layout_factory("a.pdf", PARAGRAPHS, margin=72, size=10)
    parsed = _parse(path_a)
    stream = normalise_stream(parsed.parsed_pdf, CompareConfig())
    assert " " not in stream.text
    assert "\n" not in stream.text


def test_a_sentence_never_crosses_a_paragraph(layout_factory):
    """A sentence belongs to exactly one paragraph."""
    path_a = layout_factory("a.pdf", PARAGRAPHS, margin=72, size=10)
    parsed = _parse(path_a)
    stream = normalise_stream(parsed.parsed_pdf, CompareConfig())

    for sentence in stream.sentences:
        blocks = {
            stream.block_at(position)
            for position in range(sentence.start, sentence.end)
        }
        assert len(blocks) <= 1


# --------------------------------------------------------------------------
# The parser (阶段 1)
# --------------------------------------------------------------------------


def test_the_fragments_are_merged_by_position_not_by_order():
    """Two fragments of one line are merged, whatever order they arrive in."""
    from app.parsers.pdf_layout import _Fragment

    first = _Fragment()
    first.chars = [(0.0, 0.0, 5.0, 10.0, "A")]
    second = _Fragment()
    second.chars = [(50.0, 0.0, 55.0, 10.0, "B")]
    other = _Fragment()
    other.chars = [(0.0, 20.0, 5.0, 30.0, "C")]

    rows = _group_by_baseline([other, second, first])
    assert len(rows) == 2
    line = [row for row in rows if len(row) == 2]
    assert line, "the two fragments of the first line belong together"
    assert [fragment.chars[0][4] for fragment in line[0]] == ["A", "B"]


def test_the_line_pitch_ignores_the_paragraph_distance():
    """The line spacing of a page is not the distance between paragraphs."""
    lines = [
        PdfLine(page=1, text="a", bbox=(0, 0, 100, 12), size=10, index=0),
        PdfLine(page=1, text="b", bbox=(0, 14, 100, 26), size=10, index=1),
        PdfLine(page=1, text="c", bbox=(0, 28, 100, 40), size=10, index=2),
        PdfLine(page=1, text="d", bbox=(0, 60, 100, 72), size=10, index=3),
        PdfLine(page=1, text="e", bbox=(0, 74, 100, 86), size=10, index=4),
        PdfLine(page=1, text="f", bbox=(0, 88, 100, 100), size=10, index=5),
    ]
    pitch = line_pitch(lines)
    assert pitch == pytest.approx(14.0)


def test_a_number_at_the_end_of_a_line_is_no_sentence_end():
    """A line that ends with a number is not the end of a paragraph.

    The number may have been cut in the middle of a line, so the decision
    depends on the line that follows.
    """
    from app.parsers.pdf_layout import _ends_sentence

    assert _ends_sentence("金额为1200000.", "00），分三期支付。") is False
    assert _ends_sentence("金额为1200000.00元。", "下一段。") is True
    assert _ends_sentence("left column line 0.", "left column line 1.") is True


def test_a_percentage_is_no_heading():
    """'8.6%，同比增长' is a sentence, not a heading."""
    assert _is_heading("8.6%，同比增长明显", 10.0, False, 10.0) is False
    assert _is_heading("1.2 概述", 10.0, True, 10.0) is True


def test_a_paragraph_is_not_cut_inside_a_number(layout_factory):
    """The number of an amount stays in its paragraph."""
    path_a = layout_factory("a.pdf", PARAGRAPHS, margin=130, size=12, leading=19)
    parsed = _parse(path_a)
    stream = normalise_stream(parsed.parsed_pdf, CompareConfig())
    assert "1200000.00" in stream.text
    # The amount is not cut into two paragraphs.
    texts = [block.original_text for block in parsed.document.blocks]
    assert not any(text.rstrip().endswith(".") and "1200000" in text for text in texts)


# --------------------------------------------------------------------------
# The alignment
# --------------------------------------------------------------------------


def test_repeated_content_does_not_break_the_alignment():
    """A text that occurs several times is not used as an anchor.

    The anchors of the V2 engine have to be unique, otherwise the same
    run of text could be matched with any of its copies.
    """
    from app.compare.pdf_anchor import find_anchors

    texts_a = ["说明"] * 3 + ["第一章"] + ["说明"] * 3
    texts_b = ["说明"] * 3 + ["第二章"] + ["说明"] * 3

    anchors = find_anchors(texts_a, texts_b, shingle=2)
    for anchor in anchors:
        assert texts_a[anchor.a_start : anchor.a_end] == texts_b[
            anchor.b_start : anchor.b_end
        ]


class _Stream:
    """A minimal stand-in for the normalised stream."""

    def __init__(self, sentences):
        self.sentences = sentences
        self.text = "".join(sentence.text for sentence in sentences)

    def block_at(self, position):
        return None
